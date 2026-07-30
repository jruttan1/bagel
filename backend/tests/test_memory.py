from app import crud
from app.models import User


async def test_memory_grows_updates_and_forgets(session) -> None:
    user = User(phone_number="+15550000001")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    await crud.upsert_memory(
        user.id,
        "goal.primary",
        "goal",
        "Wants long-term capital growth.",
    )
    await crud.upsert_memory(
        user.id,
        "thesis.msft",
        "thesis",
        "Views cloud distribution as the durable advantage.",
        "MSFT",
    )
    await crud.upsert_memory(
        user.id,
        "goal.primary",
        "goal",
        "Wants long-term growth without near-term withdrawals.",
    )

    memories = await crud.active_memories(user.id)
    assert len(memories) == 2
    assert next(row for row in memories if row.memory_key == "goal.primary").summary.endswith(
        "withdrawals."
    )

    assert await crud.forget_memory(user.id, "thesis.msft") is True
    assert [row.memory_key for row in await crud.active_memories(user.id)] == ["goal.primary"]
