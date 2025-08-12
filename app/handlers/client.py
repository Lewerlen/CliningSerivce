from aiogram import F, Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.handlers.states import RegistrationStates
from app.keyboards.client_kb import get_contact_keyboard
from app.services.db_queries import create_user, get_user

router = Router()


@router.message(CommandStart())
async def cmd_start(message: types.Message, session: AsyncSession, state: FSMContext):
    """Обработчик команды /start."""
    user = await get_user(session, message.from_user.id)
    if user:
        await message.answer(f"С возвращением, {user.name}!")
        # Здесь будет логика для уже зарегистрированного пользователя
    else:
        await message.answer(
            "Здравствуйте! Чтобы начать, давайте зарегистрируемся.\n\n"
            "Пожалуйста, нажмите на кнопку ниже, чтобы отправить ваш номер телефона.",
            reply_markup=get_contact_keyboard()
        )
        await state.set_state(RegistrationStates.waiting_for_phone)


@router.message(RegistrationStates.waiting_for_phone, F.contact)
async def register_phone(message: types.Message, session: AsyncSession, state: FSMContext):
    """Обработчик, который получает контакт и создает пользователя."""
    phone_number = message.contact.phone_number
    user_name = message.from_user.full_name

    await create_user(
        session,
        telegram_id=message.from_user.id,
        name=user_name,
        phone=phone_number
    )

    await message.answer(
        "Отлично! Вы успешно зарегистрированы.",
        # Убираем клавиатуру запроса контакта
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.clear()