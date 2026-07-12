"""
食堂まとめ予約 - FastAPI バックエンド
Playwright で mo-order.com を自動操作して食事を予約します。
"""
import asyncio
import re
from datetime import date
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, Page
from pydantic import BaseModel

app = FastAPI(title="食堂まとめ予約 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # デプロイ後は GitHub Pages の URL に絞る
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

SITE_URL = "https://shonanfujisawa-international-dormitory.mo-order.com/stores"


# ================================================================
# リクエスト / レスポンス型
# ================================================================

class LoginInfo(BaseModel):
    email: str
    phone: str
    room: str

class ReserveItem(BaseModel):
    date: str                          # "YYYY-MM-DD"
    meal: Literal["breakfast", "dinner"]

class ReserveRequest(BaseModel):
    login: LoginInfo
    items: list[ReserveItem]


# ================================================================
# Playwright ヘルパー
# ================================================================

async def _wait_selector(page: Page, selector: str, timeout: int = 12000):
    """セレクタが出現するまで待ちつつ要素を返す。見つからなければ None。"""
    try:
        await page.wait_for_selector(selector, timeout=timeout)
        return await page.query_selector(selector)
    except Exception:
        return None

async def _wait_all(page: Page, selector: str, min_count: int = 1, timeout: int = 12000):
    """指定数以上の要素が揃うまでポーリング。"""
    deadline = asyncio.get_event_loop().time() + timeout / 1000
    while asyncio.get_event_loop().time() < deadline:
        els = await page.query_selector_all(selector)
        if len(els) >= min_count:
            return els
        await asyncio.sleep(0.05)  # 150ms→50ms
    return await page.query_selector_all(selector)

async def _find_button(page: Page, label: str, exclude_class: str = ""):
    """ボタンをテキストで探す（クラス問わず全buttonを対象）"""
    for btn in await page.query_selector_all('button, [role="button"], .styles_cmButton__Jcmwz'):
        text = (await btn.text_content() or "").strip()
        cls  = await btn.get_attribute("class") or ""
        if label in text and (not exclude_class or exclude_class not in cls):
            return btn
    return None

async def _js_click(page: Page, selector: str):
    """Chrome拡張と同じ方法でクリック（React対応）"""
    await page.evaluate(f"""
        (function() {{
            const el = document.querySelector('{selector}');
            if (!el) return;
            ['mousedown','mouseup','click'].forEach(type =>
                el.dispatchEvent(new MouseEvent(type, {{bubbles:true, cancelable:true, view:window}}))
            );
        }})()
    """)

async def _js_click_by_text(page: Page, text: str, exclude_class: str = ""):
    """テキストでボタンを探してJSクリック（全buttonを対象）"""
    await page.evaluate(f"""
        (function() {{
            const btns = document.querySelectorAll('button, [role="button"], .styles_cmButton__Jcmwz');
            for (const b of btns) {{
                if (b.textContent.trim().includes('{text}') &&
                    (!'{exclude_class}' || !b.className.includes('{exclude_class}'))) {{
                    ['mousedown','mouseup','click'].forEach(t =>
                        b.dispatchEvent(new MouseEvent(t, {{bubbles:true, cancelable:true, view:window}}))
                    );
                    return;
                }}
            }}
        }})()
    """)


# ================================================================
# 予約ステップ (content.js をそのまま Python 移植)
# ================================================================

async def step1_go_to_stores(page: Page):
    await page.goto(SITE_URL)
    await page.wait_for_load_state("domcontentloaded", timeout=15_000)
    await _wait_selector(page, '.styles_masterStoreBox__TrvbV', timeout=10_000)

async def step2_select_store(page: Page):
    box = await _wait_selector(page, '.styles_masterStoreBox__TrvbV')
    if not box:
        raise Exception("ストアボックスが見つかりません")
    await _js_click(page, '.styles_masterStoreBox__TrvbV')
    sels = await _wait_all(page, '.styles_cmSelect__9Ud3U', min_count=1)
    if not sels:
        raise Exception("日時選択セレクトが表示されません")

async def step3_select_date_time(page: Page, date_str: str, meal: str):
    d = date.fromisoformat(date_str)
    month, day = d.month, d.day
    is_breakfast = meal == "breakfast"

    selects = await _wait_all(page, '.styles_cmSelect__9Ud3U', min_count=2)
    if not selects:
        raise Exception("日時セレクトが見つかりません")

    # ── 日付セレクト ──
    date_sel = selects[0]
    opts = await date_sel.query_selector_all('option')
    chosen = None
    for opt in opts:
        if await opt.get_attribute('disabled'):
            continue
        text = await opt.text_content() or ""
        val  = await opt.get_attribute('value') or ""
        haystack = text + val
        if (f"{month}月{day}日" in haystack
                or f"{month}/{day}" in haystack
                or f"{month:02d}/{day:02d}" in haystack):
            chosen = val
            break
    if chosen is None:
        labels = [await o.text_content() for o in opts]
        raise Exception(f"日付 {month}/{day} がセレクトに見つかりません。選択肢: {labels}")
    await date_sel.select_option(value=chosen)
    await asyncio.sleep(0.2)

    # ── 時間セレクト ──
    selects2 = await _wait_all(page, '.styles_cmSelect__9Ud3U', min_count=2)
    time_sel = selects2[1] if len(selects2) >= 2 else selects2[0]
    time_opts = await time_sel.query_selector_all('option')

    candidates = []
    for opt in time_opts:
        if await opt.get_attribute('disabled'):
            continue
        text = await opt.text_content() or ""
        val  = await opt.get_attribute('value') or ""
        m = re.search(r'(\d{1,2}):(\d{2})', f"{text} {val}")
        if not m:
            continue
        h, mn = int(m.group(1)), int(m.group(2))
        if is_breakfast and 7 <= h < 12:
            candidates.append((h, mn, val))
        elif not is_breakfast and (h > 18 or (h == 18 and mn >= 30)):
            candidates.append((h, mn, val))

    if not candidates:
        labels = [await o.text_content() for o in time_opts]
        raise Exception(f"{'朝食' if is_breakfast else '夕食'}の時間が見つかりません。選択肢: {labels}")

    candidates.sort()
    await time_sel.select_option(value=candidates[0][2])

async def step4_click_proceed(page: Page):
    btn = await _find_button(page, "商品選択に進む")
    if not btn:
        raise Exception("「商品選択に進む」ボタンが見つかりません")
    await _js_click_by_text(page, "商品選択に進む")
    await _wait_selector(page, '.styles_menuItem__g9RDF', timeout=12_000)

async def step5_select_meal_item(page: Page):
    item = await _wait_selector(page, '.styles_menuItem__g9RDF')
    if not item:
        raise Exception("食事アイテムが見つかりません")
    await _js_click(page, '.styles_menuItem__g9RDF')
    # content.js と同じ：カートに追加ボタンが出るまで最大10秒待つ
    found = await _wait_add_to_cart_btn(page, timeout=10_000)
    if not found:
        raise Exception("メニュークリック後にカートボタンが出現しませんでした")

async def _wait_add_to_cart_btn(page: Page, timeout: int = 10_000):
    """「カートに追加」ボタン（フッター以外）が出るまでポーリング"""
    deadline = asyncio.get_event_loop().time() + timeout / 1000
    while asyncio.get_event_loop().time() < deadline:
        btn = await _find_button(page, "カートに追加", exclude_class="styles_footerBtn")
        if btn:
            return btn
        await asyncio.sleep(0.05)  # 150ms→50ms
    return None

async def step6_add_to_cart(page: Page):
    btn = await _wait_add_to_cart_btn(page, timeout=10_000)
    if not btn:
        raise Exception("「カートに追加」ボタンが見つかりません")
    await _js_click_by_text(page, "カートに追加", exclude_class="styles_footerBtn")
    await _wait_selector(page, '.styles_footerBtn__E7fv0', timeout=10_000)

async def step7_go_to_cart(page: Page):
    await _wait_selector(page, '.styles_footerBtn__E7fv0', timeout=10_000)
    await page.evaluate("""
        (function() {
            const btns = document.querySelectorAll('.styles_footerBtn__E7fv0');
            const btn = [...btns].find(b => b.textContent.includes('カートを確認')) ?? btns[0];
            if (btn) ['mousedown','mouseup','click'].forEach(t =>
                btn.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window}))
            );
        })()
    """)
    await _wait_all(page, '.styles_inputWrapper__DFwnN', min_count=1, timeout=12_000)

async def step8_fill_buyer_info(page: Page, login: LoginInfo):
    wrappers = await _wait_all(page, '.styles_inputWrapper__DFwnN', min_count=1)
    inputs = []
    for w in wrappers:
        inp = await w.query_selector('input')
        if inp:
            inputs.append(inp)

    for i, val in enumerate([login.email, login.phone, login.room]):
        if i < len(inputs):
            await inputs[i].fill(val)
            await inputs[i].dispatch_event('input')
            await inputs[i].dispatch_event('change')
            await asyncio.sleep(0.2)

    await _wait_selector(page, '.styles_wrapper__ro2Qc', timeout=10_000)

async def step9_select_payment(page: Page):
    btn = await _wait_selector(page, '.styles_wrapper__ro2Qc', timeout=5_000)
    if btn:
        await page.evaluate("""
            (function() {
                const el = document.querySelector('.styles_wrapper__ro2Qc');
                if (el) ['mousedown','mouseup','click'].forEach(t =>
                    el.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window}))
                );
            })()
        """)
        await asyncio.sleep(0.2)

async def step10_confirm_order(page: Page):
    btn = await _find_button(page, "注文を確定")
    if not btn:
        raise Exception("「注文を確定」ボタンが見つかりません")
    await _js_click_by_text(page, "注文を確定")
    await asyncio.sleep(0.5)


# ================================================================
# 1件予約
# ================================================================

STEPS = [
    ("ストア一覧",     lambda p, l, it: step1_go_to_stores(p)),
    ("ストア選択",     lambda p, l, it: step2_select_store(p)),
    ("日時選択",       lambda p, l, it: step3_select_date_time(p, it.date, it.meal)),
    ("商品選択に進む", lambda p, l, it: step4_click_proceed(p)),
    ("食事選択",       lambda p, l, it: step5_select_meal_item(p)),
    ("カート追加",     lambda p, l, it: step6_add_to_cart(p)),
    ("カート確認",     lambda p, l, it: step7_go_to_cart(p)),
    ("購入者情報",     lambda p, l, it: step8_fill_buyer_info(p, l)),
    ("支払い方法",     lambda p, l, it: step9_select_payment(p)),
    ("注文確定",       lambda p, l, it: step10_confirm_order(p)),
]

async def reserve_one(page: Page, login: LoginInfo, item: ReserveItem) -> dict:
    for name, fn in STEPS:
        try:
            await fn(page, login, item)
        except Exception as e:
            if name == "支払い方法":
                continue   # 支払い選択が不要な場合はスキップ
            return {"date": item.date, "meal": item.meal,
                    "success": False, "message": f"{name}: {e}"}

    return {"date": item.date, "meal": item.meal,
            "success": True,  "message": "予約完了"}


# ================================================================
# エンドポイント
# ================================================================

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/reserve")
async def reserve(req: ReserveRequest):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        # 全件を並列実行（1件につき独立したページを使う）
        async def run(item):
            page = await browser.new_page(viewport={"width": 390, "height": 844})
            result = await reserve_one(page, req.login, item)
            await page.close()
            return result

        results = await asyncio.gather(*[run(item) for item in req.items])
        await browser.close()

    ok = sum(1 for r in results if r["success"])
    return {"ok": ok, "total": len(results), "results": list(results)}
