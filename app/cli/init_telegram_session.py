import asyncio
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError

from app.config import get_settings


def _print_auth_menu() -> str:
    print()
    print("Выберите способ входа:")
    print("1) QR-код (рекомендуется, если код по телефону не приходит)")
    print("2) Телефон + код в приложении Telegram")
    print("3) Телефон + принудительная SMS")
    choice = input("Ваш выбор [1/2/3, по умолчанию 1]: ").strip() or "1"
    return choice


async def _sign_in_qr(client: TelegramClient) -> None:
    print()
    print("На телефоне откройте: Telegram -> Настройки -> Устройства -> Подключить устройство")
    print("Отсканируйте QR ниже или откройте ссылку на телефоне.")
    print()

    qr_login = await client.qr_login()
    print(f"Ссылка для входа:\n{qr_login.url}\n")

    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(qr_login.url)
        qr.make()
        qr.print_ascii(invert=True)
    except Exception:
        print("QR в терминале не отображен. Используйте ссылку выше.")

    while True:
        try:
            await qr_login.wait(90)
            return
        except SessionPasswordNeededError:
            password = input("Please enter your 2FA password: ").strip()
            await client.sign_in(password=password)
            return
        except Exception as exc:
            print(f"QR не подтвержден ({exc}). Генерирую новый...")
            await qr_login.recreate()
            print(f"Новая ссылка:\n{qr_login.url}\n")


async def _sign_in_phone(client: TelegramClient, force_sms: bool = False) -> None:
    print()
    if force_sms:
        print("Будет запрошена доставка кода через SMS (если Telegram разрешит).")
    else:
        print("Код обычно приходит в чат 'Telegram' внутри приложения, не в SMS.")
        print("Проверьте также: Настройки -> Устройства (уведомление о входе).")
    print()

    phone = input("Please enter your phone (+7922...): ").strip()
    if not phone.startswith("+"):
        phone = f"+{phone}"

    sent = await client.send_code_request(phone, force_sms=force_sms)
    print(f"Код отправлен (тип доставки: {sent.type}).")

    for attempt in range(3):
        code = input("Please enter the code you received: ").strip()
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            return
        except SessionPasswordNeededError:
            password = input("Please enter your 2FA password: ").strip()
            await client.sign_in(password=password)
            return
        except PhoneCodeInvalidError:
            print("Неверный код. Попробуйте еще раз.")
        except PhoneCodeExpiredError:
            print("Код истек. Запрашиваю новый...")
            sent = await client.send_code_request(phone, force_sms=force_sms)
        except Exception as exc:
            if not force_sms and attempt == 0:
                print("Пробую принудительную SMS...")
                sent = await client.send_code_request(phone, force_sms=True)
                print(f"Повторная отправка (тип: {sent.type}).")
                continue
            raise exc

    raise RuntimeError("Не удалось авторизоваться после нескольких попыток.")


async def _sign_in_user(client: TelegramClient) -> None:
    choice = _print_auth_menu()
    if choice == "1":
        await _sign_in_qr(client)
    elif choice == "3":
        await _sign_in_phone(client, force_sms=True)
    else:
        await _sign_in_phone(client, force_sms=False)


async def main():
    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")

    session_path = Path(settings.telegram_session_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(session_path), settings.telegram_api_id, settings.telegram_api_hash)
    print("Важно: это вход пользовательским аккаунтом, НЕ bot token.")
    print("Bot token нужен только для TELEGRAM_BOT_TOKEN в .env (публикация).")

    await client.connect()
    if not await client.is_user_authorized():
        await _sign_in_user(client)

    me = await client.get_me()
    if getattr(me, "bot", False):
        await client.disconnect()
        session_path.unlink(missing_ok=True)
        journal = Path(str(session_path) + "-journal")
        journal.unlink(missing_ok=True)
        raise RuntimeError(
            "Сессия создана как бот. Для чтения каналов нужен user session.\n"
            "Запустите команду снова и авторизуйтесь как пользователь."
        )

    print(f"User session OK: {me.first_name} (@{me.username or 'no_username'}) id={me.id}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
