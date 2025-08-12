from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database.models import User, UserRole

async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    """Возвращает пользователя по его telegram_id или None, если пользователь не найден."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()

async def create_user(session: AsyncSession, telegram_id: int, name: str, phone: str, role: UserRole = UserRole.client) -> User:
    """Создает и возвращает нового пользователя."""
    new_user = User(
        telegram_id=telegram_id,
        name=name,
        phone=phone,
        role=role
    )
    session.add(new_user)
    await session.commit()
    return new_user