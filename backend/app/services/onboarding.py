from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OnboardingAnswer, OnboardingStep, PortfolioSnapshot, User
from app.repositories import latest_snapshot
from app.services.intelligence import IntelligenceService, IntelligenceUnavailable

QUESTION_CATEGORIES = {
    OnboardingStep.financial_position: "financial position, obligations, liquidity, and investment horizon",
    OnboardingStep.investing_style: "risk tolerance, decision style, and how actively they want to manage investments",
    OnboardingStep.portfolio_context: "the reasoning behind the most decision-relevant portfolio concentration",
}

FALLBACK_QUESTIONS = {
    OnboardingStep.financial_position: "What does this money need to do for you, and roughly when?",
    OnboardingStep.investing_style: "How do you usually decide when to buy, hold, or sell an investment?",
    OnboardingStep.portfolio_context: "Which part of your portfolio reflects your strongest current conviction?",
}


class OnboardingService:
    def __init__(self, intelligence: IntelligenceService):
        self.intelligence = intelligence

    async def first_question(self, session: AsyncSession, user: User) -> str:
        user.onboarding_step = OnboardingStep.financial_position
        await session.commit()
        return await self.question_for(session, user)

    async def handle_answer(self, session: AsyncSession, user: User, answer: str) -> str:
        step = user.onboarding_step
        if step not in QUESTION_CATEGORIES:
            return await self.first_question(session, user)
        question = str(user.profile_data.get("last_onboarding_question") or FALLBACK_QUESTIONS[step])
        session.add(OnboardingAnswer(user_id=user.id, category=step.value, question=question, answer=answer[:1000]))
        if step == OnboardingStep.financial_position:
            user.onboarding_step = OnboardingStep.investing_style
        elif step == OnboardingStep.investing_style:
            user.onboarding_step = OnboardingStep.portfolio_context
        else:
            user.onboarding_step = OnboardingStep.complete
        await session.commit()

        if user.onboarding_step == OnboardingStep.complete:
            await self._finish_profile(session, user)
            return "That’s enough for now. I’ll use it quietly in the background and text when something worth your attention changes."
        return await self.question_for(session, user)

    async def question_for(self, session: AsyncSession, user: User) -> str:
        step = user.onboarding_step
        snapshot = await latest_snapshot(session, user.id)
        try:
            question = await self.intelligence.onboarding_question(QUESTION_CATEGORIES[step], snapshot)
        except IntelligenceUnavailable:
            question = FALLBACK_QUESTIONS[step]
        profile = dict(user.profile_data)
        profile["last_onboarding_question"] = question
        user.profile_data = profile
        await session.commit()
        return question

    async def _finish_profile(self, session: AsyncSession, user: User) -> None:
        rows = (
            await session.execute(
                select(OnboardingAnswer)
                .where(OnboardingAnswer.user_id == user.id)
                .order_by(OnboardingAnswer.created_at)
            )
        ).scalars().all()
        snapshot = await latest_snapshot(session, user.id)
        answers = [{"category": row.category, "question": row.question, "answer": row.answer} for row in rows]
        try:
            profile = await self.intelligence.distill_profile(answers, snapshot)
        except IntelligenceUnavailable:
            profile = {"answers": answers, "summary": "Onboarding completed; profile distillation pending."}
        profile.pop("last_onboarding_question", None)
        user.profile_data = profile
        user.profile_summary = str(profile.get("summary") or "")
        await session.commit()

