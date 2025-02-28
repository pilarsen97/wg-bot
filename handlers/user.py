from loguru import logger
from os import remove

from aiogram import types
from aiogram.dispatcher import FSMContext

from middlewares import rate_limit
import keyboards as kb

import database
from loader import bot
from data.config import PAYMENTS_TOKEN, ADMINS, PAYMENT_CARD
from loader import vpn_config

from utils.fsm import NewConfig, NewPayment
import os




@rate_limit(limit=5)
async def cmd_start(message: types.Message) -> types.Message:
    if not message.from_user.username:
        await message.answer(f'''Привет, {message.from_user.full_name}!\nУ тебя не установлен username, установи его в настройках телеграма и напиши /start\n\
Если не знаешь как это сделать - посмотри [справку](https://silverweb.by/kak-sozdat-nik-v-telegramm/)''', parse_mode='Markdown')
        return
    if database.selector.is_exist_user(message.from_user.id):
        if database.selector.is_subscription_end(message.from_user.id):
            await message.answer(f'Привет, {message.from_user.full_name or message.from_user.username}, твоя подписка закончилась, оплати её, чтобы продолжить пользоваться VPN',
                                 reply_markup=await kb.free_user_kb(message.from_user.id))
        else:
            await message.answer(f'Привет, {message.from_user.full_name or message.from_user.username}, твоя подписка действительна до {database.selector.get_subscription_end_date(message.from_user.id)}',
                                 reply_markup=await kb.payed_user_kb())
        return

    await message.reply(f"Привет, {message.from_user.full_name or message.from_user.username}!\nЧтобы начать пользоваться VPN, оплати подписку",
                        reply_markup=await kb.free_user_kb(message.from_user.id))
    await bot.send_message(message.from_user.id, 'Подробное описание бота и его функционала доступно на [странице](https://telegra.ph/FAQ-po-botu-01-08), оплачивая подписку, вы соглашаетесь с правилами использования бота и условиями возврата средств, указанными в статье выше.',
                           parse_mode='Markdown',)
    database.insert_new_user(message)

    # notify admin about new user
    for admin in ADMINS:
        # format: Новый пользователь: Имя (id: id), username, id like code format in markdown
        await bot.send_message(admin, f'Новый пользователь: {message.from_user.full_name} (id: `{message.from_user.id}`), `{message.from_user.username}`',
                               parse_mode='markdown')


@rate_limit(limit=5)
async def cmd_pay(message: types.Message, state: FSMContext) -> types.Message:
    # на данный момент нет возможности подключить платежную систему, поэтому временно отключено
    await NewPayment.payment_image.set()
    await bot.send_message(message.from_user.id, f'В данный момент нет возможности совершить платеж в боте. \
Для оплаты подписки переведите 100₽ на карту `{PAYMENT_CARD}` и отправьте скриншот чека/операции в ответ на это сообщение.', parse_mode='Markdown', reply_markup=await kb.cancel_payment_kb())


@rate_limit(limit=5)
async def got_payment_screenshot(message: types.Message, state: FSMContext):
    if message.content_type != 'photo':
        await message.reply('Пожалуйста, отправьте скриншот чека/операции в ответ на это сообщение.')
        return

    await message.reply('Подождите, пока мы проверим вашу оплату.')
    await state.finish()
    # forwards screenshot to admin
    for admin in ADMINS:
        await message.forward(admin)
        await bot.send_message(admin, f'Пользователь {message.from_user.full_name} (id: `{message.from_user.id}`, username: `{message.from_user.username}`) оплатил подписку на VPN.\n\nПроверьте оплату и активируйте VPN для пользователя.',
                               parse_mode='markdown')


async def cancel_payment(query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await query.message.edit_text('Оплата отменена.', reply_markup=None)
# pre checkout query


async def pre_checkout_query_handler(query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)


# successful payment
async def successful_payment_handler(message: types.Message):

    database.update_user_payment(message.from_user.id)
    database.insert_new_payment(message)
    if database.selector.is_user_have_config(message.from_user.id):
        try:
            vpn_config.reconnect_payed_user(message.from_user.id)
        except Exception as e:
            logger.error(e)

    await message.answer(
        f'{message.from_user.full_name or message.from_user.username}, твой доступ к VPN продлен до {database.selector.get_subscription_end_date(message.from_user.id)}',
        reply_markup=await kb.payed_user_kb())


async def cmd_my_configs(message: types.Message):
    if database.selector.all_user_configs(message.from_user.id):
        await message.answer('Отображаю твои конфиги на кнопках', reply_markup=await kb.configs_kb(message.from_user.id))
    else:
        await message.answer('У тебя нет конфигов', reply_markup=await kb.configs_kb(message.from_user.id))


async def cmd_menu(message: types.Message):
    if database.selector.is_subscription_end(message.from_user.id):
        await message.answer('Возвращаю тебя в основное меню', reply_markup=await kb.free_user_kb(message.from_user.id))
    else:
        await message.answer('Возвращаю тебя в основное меню', reply_markup=await kb.payed_user_kb())


@ rate_limit(limit=5)
async def create_new_config(message: types.Message, state=FSMContext):
    await message.answer('Для какого устройства ты хочешь создать конфиг?', reply_markup=await kb.device_kb(message.from_user.id))
    await NewConfig.device.set()


async def device_selected(call: types.CallbackQuery, state=FSMContext):
    """
    This handler will be called when user presses `pc` or `phone` button
    """
    await state.update_data(device=call.data)
    # edit message text and delete keyboard from message
    device = "💻 ПК" if call.data.startswith("pc") else "📱 Смартфон"
    await call.message.edit_text(f'Ты выбрал {device}, приступаю к созданию конфига', reply_markup=None)
    await state.finish()

    # add +1 to user config count
    database.update_user_config_count(call.from_user.id)

    device = "PC" if call.data.startswith("pc") else "PHONE"
    user_config = vpn_config.update_server_config(username=call.from_user.username,
                                                  device=device)

    database.insert_new_config(user_id=call.from_user.id,
                               username=call.from_user.username,
                               device=device,
                               config=user_config)

    with open(f'data/temp/AW_VPN__{call.from_user.username}.conf', 'w') as f:
        f.write(user_config)

    # send config file
    await call.message.answer_document(types.InputFile(f'data/temp/AW_VPN__{call.from_user.username}.conf'),
                                       reply_markup=await kb.configs_kb(call.from_user.id))

    if device == "PHONE":
        # send qr code (create qr code from config by qrencode)
        os.system(f'qrencode -o data/temp/AW_VPN__{call.from_user.username}.png -s 10 -l H -m 2 '
                  f'< data/temp/AW_VPN__{call.from_user.username}.conf')
        await call.message.answer_photo(types.InputFile(f'data/temp/AW_VPN__{call.from_user.username}.png'),)

    # delete temp files
    try:
        remove(f'data/temp/AW_VPN__{call.from_user.username}.conf')
        if device == "PHONE":
            remove(f'data/temp/AW_VPN__{call.from_user.username}.png')
    except OSError as error:
        logger.error(
            f'Error while deleting temp files for user {call.from_user.username}; Error: {error}')


async def cancel_config_creation(call: types.CallbackQuery, state=FSMContext):
    await state.finish()
    await call.message.edit_text('Отмена создания конфига', reply_markup=None)


@ rate_limit(limit=5)
async def cmd_show_config(message: types.Message, state=FSMContext):
    if message.text.lower().endswith('пк'):
        device = 'PC'
    elif message.text.lower().endswith('смартфон'):
        device = 'PHONE'

    config = database.selector.get_user_config(
        user_id=message.from_user.id,
        config_name=f'{message.from_user.username}_{device}')

    if device == 'PC':
        with open(f'data/temp/AW_VPN__{message.from_user.username}_{device}.conf', 'w') as f:
            f.write(config)

        # send config file
        await message.answer_document(types.InputFile(f'data/temp/AW_VPN__{message.from_user.username}_{device}.conf'),)

        # delete temp files
        try:
            remove(
                f'data/temp/AW_VPN__{message.from_user.username}_{device}.conf')
        except OSError as error:
            logger.error(
                f'Error while deleting temp files for user {message.from_user.username}; Error: {error}')

    elif device == 'PHONE':
        with open(f'data/temp/AW_VPN__{message.from_user.username}_{device}.conf', 'w') as f:
            f.write(config)

        # send config file
        await message.answer_document(types.InputFile(f'data/temp/AW_VPN____{message.from_user.username}_{device}.conf'),)

        # send qr code (create qr code from config by qrencode)
        os.system(f'qrencode -o data/temp/AW_VPN__{message.from_user.username}.png -s 10 -l H -m 2'
                  f'< data/temp/AW_VPN__{message.from_user.username}_{device}.conf')
        await message.answer_photo(types.InputFile(f'data/temp/AW_VPN__{message.from_user.username}.png'),)

        # delete temp files
        try:
            remove(
                f'data/temp/AW_VPN__{message.from_user.username}_{device}.conf')
            remove(f'data/temp/AW_VPN__{message.from_user.username}.png')
        except OSError as error:
            logger.error(
                f'Error while deleting temp files for user {message.from_user.username}; Error: {error}')


@ rate_limit(limit=5)
async def cmd_support(message: types.Message):
    # send telegraph page with support info (link: https://telegra.ph/FAQ-po-botu-01-08)
    # place link inside 'странице'and parse it in markdown
    await message.answer(
        'Подробное описание бота и его функционала доступно на [странице](https://telegra.ph/FAQ-po-botu-01-08)',
        parse_mode='Markdown',
    )

    # answer with username info @pheezz as markdown
    await message.answer(
        'Если у тебя все еще остались вопросы, то ты можешь написать [мне](t.me/pheezz) лично',
        parse_mode='Markdown')


@ rate_limit(limit=5)
async def cmd_show_end_time(message: types.Message):
    # show user end time
    await message.answer(
        f'''{message.from_user.full_name or message.from_user.username},
твой доступ к VPN закончится {database.selector.get_subscription_end_date(message.from_user.id)}''')


@ rate_limit(limit=2)
async def cmd_show_subscription(message: types.Message):
    await message.answer(
        f'''{message.from_user.full_name or message.from_user.username},
здесь ты можешь распорядиться своей подпиской''',
        reply_markup=await kb.subscription_management_kb())


@ rate_limit(limit=3600)
async def cmd_reboot_wg_service(message: types.Message):
    await message.answer('Перезагрузка сервиса WireGuard...')
    vpn_config.restart_service()
    await message.answer('Сервис WireGuard перезагружен')