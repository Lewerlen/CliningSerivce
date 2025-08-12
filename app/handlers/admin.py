from aiogram import Router, types
from aiogram.filters import CommandStart

router = Router()

@router.message(CommandStart())
async def cmd_start_admin(message: types.Message):
    await message.answer("Добро пожаловать в панель администратора.")