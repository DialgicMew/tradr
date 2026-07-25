"""Telegram bot — the primary interaction loop (ARCHITECTURE §3).

Outbound: `send_signals()` pushes signal cards (raw Bot API, callable from the sync
runner). Inbound: long-polling handlers for structured trade logging and the
confirm-each lifecycle. No inbound port is opened (long-polling is outbound only).

Trade logging is STRUCTURED (decided): /buy TICKER QTY PRICE [YYYY-MM-DD], /sell …,
/reserve AMOUNT [note]. Each write echoes a confirmation.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

from tradr.config import get_settings
from tradr.db import session_scope
from tradr.services import ledger as ledger_svc
from tradr.services import signals as sig_svc

log = logging.getLogger("tradr.bot")
_API = "https://api.telegram.org/bot{token}/{method}"


# --- outbound (sync, used by runner) ----------------------------------------

def _post(method: str, payload: dict) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        log.info("no telegram token — skipping %s", method)
        return
    url = _API.format(token=settings.telegram_bot_token, method=method)
    with httpx.Client(timeout=10.0) as c:
        r = c.post(url, json=payload)
        r.raise_for_status()


def _format_signal(sig: dict) -> str:
    icon = {"BUY": "🟢", "SELL": "🔴", "DEPLOY": "🔵", "REPLACE": "🔁"}.get(sig["action"], "•")
    lines = [f"{icon} *{sig['action']} {sig['ticker']}*  ({sig['rule']})", sig["cause"]]
    if sig["size_dollars"]:
        lines.append(f"→ ~{sig['size_shares']:.0f} sh  (${sig['size_dollars']:,.0f})")
    for w in sig.get("warnings", []):
        lines.append(f"⚠️ {w}")
    for n in sig.get("notes", []):
        lines.append(f"· {n}")
    lines.append("_Advisory only — you execute. Confirm the fill after you trade._")
    return "\n".join(lines)


def send_signals(payloads: list[dict]) -> None:
    settings = get_settings()
    for sig in payloads:
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Done", "callback_data": f"done:{sig['id']}"},
            {"text": "⏸ Skip", "callback_data": f"skip:{sig['id']}"},
        ]]}
        _post("sendMessage", {
            "chat_id": settings.telegram_chat_id,
            "text": _format_signal(sig),
            "parse_mode": "Markdown",
            "reply_markup": keyboard,
        })


# --- inbound (async, long-polling) ------------------------------------------

def build_application():  # pragma: no cover — requires network/token
    from telegram import Update
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    settings = get_settings()

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "tradr — advisory only, I never trade.\n"
            "/buy TICKER QTY PRICE [YYYY-MM-DD]\n"
            "/sell TICKER QTY PRICE [YYYY-MM-DD]\n"
            "/reserve AMOUNT [note]\n/holdings\n/status")

    async def buy_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        side = "buy" if update.message.text.startswith("/buy") else "sell"
        try:
            _, ticker, qty, price, *rest = update.message.text.split()
            td = date.fromisoformat(rest[0]) if rest else date.today()
            with session_scope() as s:
                ledger_svc.record_trade(s, ticker=ticker, side=side, qty=float(qty),
                                        price=float(price), trade_date=td)
            await update.message.reply_text(
                f"✅ logged {side} {float(qty):g} {ticker.upper()} @ ${float(price):g} ({td})")
        except Exception as e:
            await update.message.reply_text(f"⚠️ usage: /{side} TICKER QTY PRICE [YYYY-MM-DD]\n({e})")

    async def reserve(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            _, amount, *note = update.message.text.split()
            with session_scope() as s:
                ledger_svc.add_reserve(s, kind="external", amount=float(amount),
                                       note=" ".join(note))
                bal = ledger_svc.reserve_balance(s)
            await update.message.reply_text(f"✅ reserve {float(amount):+,.0f} → balance ${bal:,.0f}")
        except Exception as e:
            await update.message.reply_text(f"⚠️ usage: /reserve AMOUNT [note]\n({e})")

    async def holdings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        with session_scope() as s:
            pos = ledger_svc.derive_positions(s)
            bal = ledger_svc.reserve_balance(s)
        if not pos:
            await update.message.reply_text("No holdings yet.")
            return
        lines = [f"{p.ticker}: {p.shares:g} sh @ ${p.avg_basis:,.2f} ({p.tier})" for p in pos]
        lines.append(f"\nReserve: ${bal:,.0f}")
        await update.message.reply_text("\n".join(lines))

    async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        await q.answer()
        action, sid = q.data.split(":")
        sid = int(sid)
        if action == "skip":
            with session_scope() as s:
                sig_svc.skip_signal(s, sid)
            await q.edit_message_text(q.message.text + "\n\n⏸ Skipped (snoozed).")
        else:  # done → collect the fill
            ctx.user_data["awaiting_fill"] = sid
            await q.edit_message_text(
                q.message.text + "\n\n✅ Reply with the fill: `QTY PRICE`", parse_mode="Markdown")

    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        sid = ctx.user_data.get("awaiting_fill")
        if not sid:
            return
        try:
            qty, price = update.message.text.split()
            with session_scope() as s:
                sig_svc.confirm_signal(s, sid, fill_qty=float(qty), fill_price=float(price))
            ctx.user_data.pop("awaiting_fill", None)
            await update.message.reply_text(f"✅ confirmed signal #{sid}: {float(qty):g} @ ${float(price):g}")
        except Exception as e:
            await update.message.reply_text(f"⚠️ reply with `QTY PRICE` ({e})")

    async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        import httpx as _httpx

        base = f"http://{settings.api_host}:{settings.api_port}/api/portfolio/summary"
        try:
            with _httpx.Client(timeout=5.0) as c:
                data = c.get(base).json()
            await update.message.reply_text(
                f"Invested: ${data['invested_equity']:,.0f}\n"
                f"Unrealized: {(data.get('unrealized_gain_pct') or 0):+.1%}\n"
                f"Reserve: ${data['reserve_balance']:,.0f}\n"
                f"Core weight: {(data.get('core_weight') or 0):.0%}")
        except Exception as e:
            await update.message.reply_text(f"⚠️ status unavailable ({e})")

    app_ = Application.builder().token(settings.telegram_bot_token).build()
    app_.add_handler(CommandHandler("start", start))
    app_.add_handler(CommandHandler(["buy", "sell"], buy_sell))
    app_.add_handler(CommandHandler("reserve", reserve))
    app_.add_handler(CommandHandler("holdings", holdings))
    app_.add_handler(CommandHandler("status", status))
    app_.add_handler(CallbackQueryHandler(on_button))
    app_.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app_


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=get_settings().log_level)
    from tradr.db import init_db

    init_db()
    build_application().run_polling()


if __name__ == "__main__":  # pragma: no cover
    main()
