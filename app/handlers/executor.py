from aiogram import Router, types
from aiogram.filters import CommandStart

router = Router()

@router.message(CommandStart())
async def cmd_start_executor(message: types.Message):
    await message.answer("Привет, я бот для исполнителей.")