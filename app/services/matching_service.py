from __future__ import annotations

import json
from dataclasses import dataclass

from app.adapters.llm_client import LLMClient
from app.config import Settings
from app.services.cost_service import CostService
from app.schemas.common import ReviewItem
from app.schemas.projects import ContextRecord, ProjectMatchCandidate, ProjectMatchResult, ProjectRecord
from app.utils.confidence import build_confidence
from app.utils.text import similarity


class MatchingService:
    def __init__(self, settings: Settings, llm_client: LLMClient | None = None, cost_service: CostService | None = None):
        self.settings = settings
        self.llm = llm_client
        self.cost_service = cost_service

    def match_project(self, query: str, projects: list[ProjectRecord], metadata: dict | None = None) -> ProjectMatchResult:
        sender = (metadata or {}).get("sender", "")
        query_norm = query.strip().lower()

        @dataclass
        class _ScoredProject:
            project: ProjectRecord
            lexical: float
            profile: float
            sender_bonus: float
            combined: float

        def _profile_text(project: ProjectRecord) -> str:
            return " | ".join(
                [
                    project.title,
                    project.description or "",
                    project.area_path or "",
                    project.project_path or "",
                    " ".join(project.tags),
                ]
            )

        def _sender_bias(project: ProjectRecord, sender_value: str) -> float:
            if not sender_value:
                return 0.0
            sender_norm = sender_value.strip().lower()
            _, _, sender_domain = sender_norm.partition("@")
            bonus = 0.0
            routing = self.settings.project_routing
            haystack = " ".join([(project.area_path or "").lower(), (project.project_path or "").lower(), project.title.lower(), (project.description or "").lower()])
            for rule in routing.sender_rules:
                if sender_norm != rule.sender.strip().lower():
                    continue
                if rule.area_contains and not any(token.lower() in (project.area_path or "").lower() for token in rule.area_contains):
                    continue
                if rule.project_contains and not any(token.lower() in haystack for token in rule.project_contains):
                    continue
                bonus += rule.score_bonus
            if sender_domain:
                for rule in routing.domain_rules:
                    if sender_domain != rule.domain.strip().lower().lstrip("@"):
                        continue
                    if rule.area_contains and not any(token.lower() in (project.area_path or "").lower() for token in rule.area_contains):
                        continue
                    if rule.project_contains and not any(token.lower() in haystack for token in rule.project_contains):
                        continue
                    bonus += rule.score_bonus
            return min(routing.max_sender_bonus, bonus)

        routing = self.settings.project_routing
        scored_projects: list[_ScoredProject] = []
        for project in projects:
            lexical = similarity(query, project.title)
            title_norm = project.title.strip().lower()
            if title_norm and (title_norm in query_norm or query_norm in title_norm):
                lexical = max(lexical, 0.92)
            profile = similarity(query, _profile_text(project))
            sender_bonus = _sender_bias(project, sender)
            combined = (
                routing.lexical_weight * lexical
                + routing.profile_weight * profile
                + routing.sender_bias_weight * sender_bonus
            )
            scored_projects.append(
                _ScoredProject(
                    project=project,
                    lexical=round(lexical, 3),
                    profile=round(profile, 3),
                    sender_bonus=round(sender_bonus, 3),
                    combined=round(combined, 3),
                )
            )

        scored_projects.sort(key=lambda item: item.combined, reverse=True)
        scored = sorted(
            [ProjectMatchCandidate(id=item.project.id, title=item.project.title, score=item.combined) for item in scored_projects],
            key=lambda item: item.score,
            reverse=True,
        )
        if not scored:
            confidence = build_confidence(0.0, "No projects available for matching.", True)
            return ProjectMatchResult(matched=False, candidates=[], confidence=confidence)

        direct_title_matches = [item for item in scored_projects if item.project.title.strip().lower() and item.project.title.strip().lower() in query_norm]
        if len(direct_title_matches) == 1:
            selected = direct_title_matches[0].project
            return ProjectMatchResult(
                matched=True,
                selected_project=selected,
                candidates=scored[:3],
                confidence=build_confidence(max(0.9, direct_title_matches[0].combined), f"Direct title token match for '{selected.title}'.", False),
            )

        best = scored[0]
        second = scored[1] if len(scored) > 1 else None
        if best.score >= self.settings.confidence.auto_create and (second is None or best.score - second.score >= 0.08):
            selected = next(project for project in projects if project.id == best.id)
            return ProjectMatchResult(
                matched=True,
                selected_project=selected,
                candidates=scored[:3],
                confidence=build_confidence(best.score, f"Matched project '{selected.title}' using title/profile/sender signals.", False),
            )

        if best.score >= self.settings.confidence.review_required and (second is None or best.score - second.score >= 0.12):
            selected = next(project for project in projects if project.id == best.id)
            return ProjectMatchResult(
                matched=True,
                selected_project=selected,
                candidates=scored[:3],
                confidence=build_confidence(best.score, f"Matched project '{selected.title}' with moderate confidence and clear lead.", False),
            )

        review = ReviewItem(
            item_type="project_match",
            reason="Project name matching was ambiguous.",
            options=[candidate.model_dump() for candidate in scored[:3]],
            confidence=build_confidence(best.score, "Top project candidate requires review.", True),
        )

        if self._can_use_ambiguous_llm() and scored:
            option_payload = [
                {
                    "title": item.project.title,
                    "area_path": item.project.area_path,
                    "project_path": item.project.project_path,
                    "description": item.project.description,
                    "score": item.combined,
                }
                for item in scored_projects[:5]
            ]
            llm_selected = self._llm_select_best(
                query,
                [candidate.title for candidate in scored[:5]],
                metadata={**(metadata or {}), "project_options": option_payload},
            )
            if llm_selected:
                selected = next((project for project in projects if project.title == llm_selected), None)
                if selected:
                    return ProjectMatchResult(
                        matched=True,
                        selected_project=selected,
                        candidates=scored[:5],
                        confidence=build_confidence(0.8, f"LLM disambiguated project to '{selected.title}'.", False),
                    )

        return ProjectMatchResult(
            matched=False,
            candidates=scored[:3],
            confidence=review.confidence,
            review_items=[review],
        )

    def match_contexts(
        self,
        requested_contexts: list[str],
        available_contexts: list[ContextRecord],
        metadata: dict | None = None,
    ) -> tuple[list[str], list[ReviewItem]]:
        def _normalize(value: str) -> str:
            return "".join(ch.lower() for ch in value.strip() if ch.isalnum())

        def _context_profile_text(context: ContextRecord) -> str:
            return " | ".join([context.title or "", context.description or ""])

        def _is_agenda_context(context: ContextRecord) -> bool:
            haystack = _context_profile_text(context).lower()
            return "agenda" in haystack or "1:1" in haystack or "one on one" in haystack

        def _looks_like_agenda_request(requested: str, meta: dict | None) -> bool:
            requested_text = (requested or "").lower()
            sender = ((meta or {}).get("sender") or "").lower()
            return any(token in requested_text for token in ["agenda", "1:1", "one on one", "with "]) or "katie" in sender

        def _find_email_computer_context() -> ContextRecord | None:
            preferred_tokens = ("computer", "pc", "online", "web")
            for context in available_contexts:
                title = context.title.lower()
                if any(token in title for token in preferred_tokens):
                    return context
            return None

        if not requested_contexts or not available_contexts:
            return requested_contexts, []

        selected: list[str] = []
        review_items: list[ReviewItem] = []
        is_email_source = ((metadata or {}).get("source") or "").strip().lower() == "email"
        for requested in requested_contexts:
            requested_norm = _normalize(requested)

            # Deterministic exact/alias match first.
            exact = next((context for context in available_contexts if _normalize(context.title) == requested_norm), None)
            if exact:
                selected.append(exact.id)
                continue

            # For email-origin tasks, strongly bias to Computer-like context when requested.
            if is_email_source and requested_norm in {"computer", "email", "online", "web", "internet"}:
                computer_ctx = _find_email_computer_context()
                if computer_ctx:
                    selected.append(computer_ctx.id)
                    continue

            scored = sorted(
                [
                    (
                        context,
                        max(
                            similarity(requested, context.title),
                            similarity(requested, _context_profile_text(context)),
                        ),
                    )
                    for context in available_contexts
                ],
                key=lambda pair: pair[1],
                reverse=True,
            )

            # Extra safety: avoid agenda contexts for generic email/computer requests.
            if is_email_source and requested_norm in {"computer", "email", "online", "web", "internet"}:
                if scored and _is_agenda_context(scored[0][0]):
                    if not _looks_like_agenda_request(requested, metadata):
                        computer_ctx = _find_email_computer_context()
                        if computer_ctx:
                            selected.append(computer_ctx.id)
                            continue
            best, score = scored[0]
            second_score = scored[1][1] if len(scored) > 1 else 0.0
            if score >= self.settings.confidence.auto_create and (score - second_score >= 0.08):
                selected.append(best.id)
                continue

            review_items.append(
                ReviewItem(
                    item_type="context_match",
                    reason=f"Context '{requested}' was ambiguous.",
                    options=[{"id": c.id, "title": c.title, "score": round(s, 3)} for c, s in scored[:3]],
                    confidence=build_confidence(score, "Top context candidate requires review.", True),
                )
            )
            llm_choice = None
            if self._can_use_ambiguous_llm():
                llm_choice = self._llm_select_best(requested, [c.title for c, _ in scored[:5]], metadata=metadata)
            if llm_choice:
                winner = next((context for context, _ in scored if context.title == llm_choice), None)
                if winner:
                    selected.append(winner.id)
                    continue

            # Conservative behavior: do not silently select an ambiguous context.
            # For email tasks, prefer Computer context if available as safe default.
            if is_email_source:
                computer_ctx = _find_email_computer_context()
                if computer_ctx:
                    selected.append(computer_ctx.id)

        return selected, review_items

    def build_project_creation_review(self, project_name: str) -> ReviewItem:
        return ReviewItem(
            item_type="project_creation",
            reason="No active project confidently matched this email. Review creating a new project.",
            options=[{"title": project_name, "tags": [self.settings.review_project_tag]}],
            confidence=build_confidence(0.55, "Suggested new project requires manual review.", True),
        )

    def _can_use_ambiguous_llm(self) -> bool:
        return bool(self.llm and self.settings.llm.enabled and self.settings.llm.use_for_ambiguous_matching)

    def _llm_select_best(self, query: str, options: list[str], metadata: dict | None = None) -> str | None:
        if not self.llm or not options:
            return None
        prompt = json.dumps({"query": query, "options": options})
        result = self.llm.chat_json(
            system_prompt=(
                "Choose the single best option for the query. "
                "Return JSON with key 'selected'. If none fit, return selected as empty string."
            ),
            user_prompt=prompt,
            model=self._model_for_ambiguous_matching(),
            operation="ambiguous_match",
            metadata=metadata,
        )
        selected = (result.get("selected") or "").strip()
        return selected if selected in options else None

    def _model_for_ambiguous_matching(self) -> str:
        tier = self.settings.llm.ambiguous_matching_tier
        if self.cost_service:
            return self.cost_service.get_tier_model(tier)
        if tier == "fast":
            return self.settings.llm.cheap_model
        if tier == "balanced":
            return self.settings.llm.standard_model
        if tier == "smart":
            return self.settings.llm.premium_model
        return self.settings.llm.best_model or self.settings.llm.premium_model
