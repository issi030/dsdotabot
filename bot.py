
import asyncio
import io
import re

import discord
from discord import app_commands
from discord.ext import commands
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

import tempfile
from pathlib import Path

SITE = "https://china8sd.github.io/who-ruined/"
MAX_CONCURRENT_ANALYSES = 1

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
analysis_semaphore = asyncio.Semaphore(MAX_CONCURRENT_ANALYSES)


async def analyze_match(match_id: str):
    async with analysis_semaphore:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            try:
                page = await browser.new_page(
                    accept_downloads=True,
                    viewport={"width": 1440, "height": 1000},
                )

                await page.goto(
                    f"{SITE}?match={match_id}",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                # Ждём, пока сайт закончит анализ.
                await page.wait_for_selector(
                    "#result:not(.hidden)",
                    timeout=120000,
                )

                await page.wait_for_function(
                    """() => {
                        const el = document.querySelector("#culpritName");
                        return el && el.innerText.trim()
                            && el.innerText.trim() !== "—";
                    }""",
                    timeout=30000,
                )

                name = (
                    await page.locator("#culpritName").inner_text()
                ).strip()

                score = (
                    await page.locator("#culpritScore").inner_text()
                ).strip()

                confidence = (
                    await page.locator("#confidence").inner_text()
                ).strip()

                summary = (
                    await page.locator("#summary").inner_text()
                ).strip()

                # Получаем подробные причины из окна анализа.
                await page.locator("#whyRuinerBtn").click()

                await page.wait_for_selector(
                    "#whyRuinerModal:not(.hidden) #whyRuinerContent",
                    timeout=15000,
                )

                reasons = (
                    await page.locator("#whyRuinerContent").inner_text()
                ).strip()

                report = (
                    f"## WHO RUINED? — Матч {match_id}\n"
                    f"**Предполагаемый руинер:** {name}\n"
                    f"**Влияние на поражение:** {score}\n"
                    f"**Уверенность модели:** {confidence}\n\n"
                    f"**Вердикт**\n{summary}\n\n"
                    f"**Почему именно он?**\n{reasons}\n\n"
                    f"[Открыть полный анализ]({SITE}?match={match_id})\n"
                    "_Оценка модели, а не доказательство единоличной вины игрока._"
                )

                
                # Скачиваем оригинальную карточку сайта.
                image_bytes = None

                try:
                    async with page.expect_download(
                        timeout=15000
                    ) as download_event:
                        await page.locator("#shareCard").click()

                    download = await download_event.value

                    with tempfile.TemporaryDirectory() as tmp:
                        image_path = Path(tmp) / "report.png"
                        await download.save_as(str(image_path))
                        image_bytes = image_path.read_bytes()

                except PlaywrightTimeoutError:
                    image_bytes = await page.locator(
                        "#result"
                    ).screenshot(type="png")

                if not image_bytes:
                    raise RuntimeError(
                        "Не удалось получить изображение отчёта."
                    )

                return report, image_bytes


            finally:
                await browser.close()


@bot.tree.command(
    name="ruin",
    description="Анализ матча Dota 2 на Who Ruined?"
)
@app_commands.describe(match_id="ID матча Dota 2")
async def ruin(
    interaction: discord.Interaction,
    match_id: str,
):
    if not re.fullmatch(r"\d{5,20}", match_id):
        await interaction.response.send_message(
            "Укажи корректный числовой Match ID.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        report, image_bytes = await asyncio.wait_for(
            analyze_match(match_id),
            timeout=180,
        )

        # Учитываем лимит Discord на длину сообщения.
        if len(report) > 1900:
            report = report[:1800] + (
                f"\n\n[Полный анализ]({SITE}?match={match_id})"
            )

        image = discord.File(
            fp=io.BytesIO(image_bytes),
            filename=f"who-ruined-{match_id}.png",
        )

        await interaction.followup.send(
            content=report,
            file=image,
        )

    except asyncio.TimeoutError:
        await interaction.followup.send(
            "Анализ занял слишком много времени. "
            "Попробуй повторить команду позже."
        )

    except Exception as exc:
        print(f"Ошибка анализа матча {match_id}: {exc}")

        await interaction.followup.send(
            f"Не удалось получить анализ матча {match_id}. "
            f"Проверь его на сайте: {SITE}?match={match_id}"
        )


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Бот запущен: {bot.user}")


if __name__ == "__main__":
    import os

    token = os.getenv("DISCORD_TOKEN")

    if not token:
        raise RuntimeError(
            "Добавь DISCORD_TOKEN в переменные окружения."
        )

    bot.run(token)

