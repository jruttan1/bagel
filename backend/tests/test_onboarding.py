import pytest

from app import crud, messages
from app.models import OnboardingStep, User


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
    intelligence = FakeIntelligence()

    await messages.question_for(intelligence, user)
    user = await crud.user_by_id(user.id)
    await messages.handle_answer(intelligence, user, "A long-term pool I do not need soon.")
    user = await crud.user_by_id(user.id)
    assert user.onboarding_step == OnboardingStep.investing_style
    await messages.handle_answer(intelligence, user, "I review decisions carefully and trade infrequently.")
    user = await crud.user_by_id(user.id)
    assert user.onboarding_step == OnboardingStep.portfolio_context
    final = await messages.handle_answer(
        intelligence, user, "My highest conviction is in durable cash flows."
    )
    user = await crud.user_by_id(user.id)

    assert user.onboarding_step == OnboardingStep.complete
    assert user.profile_data["risk_capacity"] == "moderate"
    assert "That’s enough" in final
