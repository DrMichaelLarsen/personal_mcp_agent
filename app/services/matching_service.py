from __future__ import annotations

import json

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
        scored = sorted(
            [ProjectMatchCandidate(id=p.id, title=p.title, score=round(similarity(query, p.title), 3)) for p in projects],
            key=lambda item: item.score,
            reverse=True,
        )
        if not scored:
            confidence = build_confidence(0.0, "No projects available for matching.", True)
            return ProjectMatchResult(matched=False, candidates=[], confidence=confidence)

        best = scored[0]
        second = scored[1] if len(scored) > 1 else None
        if best.score >= self.settings.confidence.auto_create and (second is None or best.score - second.score >= 0.08):
            selected = next(project for project in projects if project.id == best.id)
            return ProjectMatchResult(
                matched=True,
                selected_project=selected,
                candidates=scored[:3],
                confidence=build_confidence(best.score, f"Matched project '{selected.title}' from fuzzy name lookup.", False),
            )

        review = ReviewItem(
            item_type="project_match",
            reason="Project name matching was ambiguous.",
            options=[candidate.model_dump() for candidate in scored[:3]],
            confidence=build_confidence(best.score, "Top project candidate requires review.", True),
        )

        if self._can_use_ambiguous_llm() and scored:
            llm_selected = self._llm_select_best(query, [candidate.title for candidate in scored[:5]], metadata=metadata)
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
        if not requested_contexts or not available_contexts:
            return requested_contexts, []

        selected: list[str] = []
        review_items: list[ReviewItem] = []
        for requested in requested_contexts:
            scored = sorted(
                [(context, similarity(requested, context.title)) for context in available_contexts],
                key=lambda pair: pair[1],
                reverse=True,
            )
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
            selected.append(best.id)

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
