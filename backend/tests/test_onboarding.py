import pytest

from app.models import OnboardingStep, User
from app.services.onboarding import OnboardingService


class FakeIntelligence:
    async def onboarding_question(self, category, snapshot):
        return f"What should I know about your {category.split(',')[0]}?"

    async def distill_profile(self, answers, snapshot):
        return {
            "summary": "Long horizon with flexible liquidity needs.",
            "risk_capacity": "moderate",
        }


@pytest.mark.asyncio
async def test_onboarding_advances_and_stores_internal_profile(session) -> None:
    user = User(phone_number="+14165550123", onboarding_step=OnboardingStep.financial_position)
    session.add(user)
    await session.commit()
    onboarding = OnboardingService(FakeIntelligence())

    await onboarding.question_for(session, user)
    await onboarding.handle_answer(session, user, "A long-term pool I do not need soon.")
    assert user.onboarding_step == OnboardingStep.investing_style
    await onboarding.handle_answer(session, user, "I review decisions carefully and trade infrequently.")
    assert user.onboarding_step == OnboardingStep.portfolio_context
    final = await onboarding.handle_answer(session, user, "My highest conviction is in durable cash flows.")

    assert user.onboarding_step == OnboardingStep.complete
    assert user.profile_data["risk_capacity"] == "moderate"
    assert "That’s enough" in final
