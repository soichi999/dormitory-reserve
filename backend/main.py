"""
食堂まとめ予約 - FastAPI バックエンド
GraphQL API を直接呼び出して食事を予約します（Playwright不使用）。
"""
import asyncio
import json
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import webauthn as wa
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    UserVerificationRequirement,
    ResidentKeyRequirement,
    RegistrationCredential,
    AuthenticationCredential,
    AuthenticatorAttestationResponse,
    AuthenticatorAssertionResponse,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

# ================================================================
# 設定
# ================================================================

_rate: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT = 11

GRAPHQL        = "https://shonanfujisawa-international-dormitory.mo-order.com/api/graphql"
DELIVERY_STORE = "2db98ea3-f9fb-4b3b-86cc-e18677b01491"
SITE_ID        = "d1161f9d-ab82-41ea-ad43-bf047d86b731"

RP_ID   = "soichi999.github.io"
RP_NAME = "食堂まとめ予約"
ORIGIN  = "https://soichi999.github.io"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

_pk_challenges: dict[str, dict] = {}  # {challenge_id: {challenge: bytes, exp: float}}

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Origin": "https://shonanfujisawa-international-dormitory.mo-order.com",
    "Referer": "https://shonanfujisawa-international-dormitory.mo-order.com/",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
}

app = FastAPI(title="食堂まとめ予約 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ================================================================
# Supabase ヘルパー
# ================================================================

def _sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

async def sb_get(table: str, params: dict):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=_sb_headers())
        r.raise_for_status()
        return r.json()

async def sb_post(table: str, data: dict):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{SUPABASE_URL}/rest/v1/{table}", json=data,
                         headers={**_sb_headers(), "Prefer": "return=minimal"})
        r.raise_for_status()

async def sb_patch(table: str, params: dict, data: dict):
    async with httpx.AsyncClient() as c:
        r = await c.patch(f"{SUPABASE_URL}/rest/v1/{table}", params=params, json=data,
                          headers={**_sb_headers(), "Prefer": "return=minimal"})
        r.raise_for_status()


# ================================================================
# セッション検証
# ================================================================

async def verify_session(authorization: str) -> str:
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(401, "ログインが必要です")
    now = datetime.now(timezone.utc).isoformat()
    rows = await sb_get("passkey_sessions", {"token": f"eq.{token}", "expires_at": f"gt.{now}"})
    if not rows:
        raise HTTPException(401, "セッションが無効または期限切れです。再ログインしてください。")
    return rows[0]["user_id"]


# ================================================================
# リクエスト / レスポンス型
# ================================================================

class LoginInfo(BaseModel):
    email: str
    phone: str
    room: str

class ReserveItem(BaseModel):
    date: str
    meal: Literal["breakfast", "dinner"]

class ReserveRequest(BaseModel):
    login: LoginInfo
    items: list[ReserveItem]
    dry_run: bool = False

class MenuSaveReq(BaseModel):
    menu: list[dict]  # [{"date":"2026-07-28","b":"焼きそば","d":"回鍋肉"}, ...]

class PkRegStartReq(BaseModel):
    username: str

class PkRegFinishReq(BaseModel):
    challenge_id: str
    username: str
    credential: dict

class PkLoginFinishReq(BaseModel):
    challenge_id: str
    credential: dict


# ================================================================
# GraphQL クエリ / ミューテーション
# ================================================================

Q_GET_STORE = """
query GetDeliveryStore($id: UUID!) {
  deliveryStore(id: $id) {
    datePeriods {
      date
      periods { startTime endTime }
    }
  }
}"""

Q_GET_MENUS = """
query GetStoreMenus($deliveryStoreId: UUID!, $pickupTime: String!, $orderMethods: [OrderMethod!]!) {
  deliveryStoreMenus(deliveryStoreId: $deliveryStoreId, pickupTime: $pickupTime) {
    id
    deliveryStoreCategories {
      deliveryStoreItems(pickupTime: $pickupTime, orderMethods: $orderMethods) {
        id
        taxIncludedTakeoutPrice
      }
    }
  }
}"""

Q_GET_MENU_TITLES = """
query GetStoreMenus($deliveryStoreId: UUID!, $pickupTime: String!, $orderMethods: [OrderMethod!]!) {
  deliveryStoreMenus(deliveryStoreId: $deliveryStoreId, pickupTime: $pickupTime) {
    deliveryStoreCategories {
      deliveryStoreItems(pickupTime: $pickupTime, orderMethods: $orderMethods) {
        title { translation { ja } }
        taxIncludedTakeoutPrice
      }
    }
  }
}"""

M_UPSERT_CART = """
mutation UpsertCart($cartInput: CartInput!) {
  upsertCart(input: $cartInput) { id }
}"""

M_CREATE_ORDER = """
mutation CreateTakeoutOrder($input: TakeOrderInput!) {
  createTakeoutOrder(input: $input) { id }
}"""


# ================================================================
# ヘルパー（予約）
# ================================================================

async def gql(client: httpx.AsyncClient, operation: str, query: str, variables: dict) -> dict:
    resp = await client.post(
        GRAPHQL,
        json={"operationName": operation, "query": query, "variables": variables},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise Exception(f"GraphQL: {data['errors'][0].get('message', data['errors'])}")
    return data["data"]


def to_hhmm(t: str) -> str:
    return re.sub(r"[^0-9]", "", t)[:4]


async def get_pickup_time(client: httpx.AsyncClient, date_str: str, is_breakfast: bool) -> str:
    data = await gql(client, "GetDeliveryStore", Q_GET_STORE, {"id": DELIVERY_STORE})
    date_compact = date_str.replace("-", "")
    for dp in data["deliveryStore"]["datePeriods"]:
        if dp["date"].replace("-", "") != date_compact:
            continue
        for period in dp["periods"]:
            hhmm = to_hhmm(period["startTime"])
            h = int(hhmm[:2])
            if is_breakfast and 6 <= h < 12:
                return date_compact + hhmm
            if not is_breakfast and h >= 17:
                return date_compact + hhmm
    return date_compact + ("0800" if is_breakfast else "1830")


async def get_menu_item(client: httpx.AsyncClient, pickup_time: str, is_breakfast: bool):
    data = await gql(client, "GetStoreMenus", Q_GET_MENUS, {
        "deliveryStoreId": DELIVERY_STORE,
        "pickupTime": pickup_time,
        "orderMethods": ["TAKE_OUT"],
    })
    target_price = 300 if is_breakfast else 500
    for menu in data["deliveryStoreMenus"]:
        for cat in menu["deliveryStoreCategories"]:
            for si in cat["deliveryStoreItems"]:
                if si["taxIncludedTakeoutPrice"] == target_price:
                    return menu["id"], si["id"]
    raise Exception(f"{'朝食(¥300)' if is_breakfast else '夕食(¥500)'}メニューが見つかりません")


# ================================================================
# 献立の自動取得（Camel Order側のメニュー名を献立表に反映）
# ================================================================

def parse_dish_name(title: str) -> str:
    # 例: "【8/26朝食】親子煮（Chicken egg binding）" → "親子煮"
    name = re.sub(r"^【[^】]*】", "", title).strip()
    name = re.sub(r"[（(]\s*[A-Za-z][^）)]*[）)]\s*$", "", name).strip()
    return name


async def get_dish_name(client: httpx.AsyncClient, pickup_time: str, target_price: int) -> str:
    data = await gql(client, "GetStoreMenus", Q_GET_MENU_TITLES, {
        "deliveryStoreId": DELIVERY_STORE,
        "pickupTime": pickup_time,
        "orderMethods": ["TAKE_OUT"],
    })
    for menu in data["deliveryStoreMenus"]:
        for cat in menu["deliveryStoreCategories"]:
            for si in cat["deliveryStoreItems"]:
                if si["taxIncludedTakeoutPrice"] == target_price:
                    return parse_dish_name(si["title"]["translation"]["ja"])
    return ""


async def fetch_weekly_menu_from_camel() -> list[dict]:
    async with httpx.AsyncClient() as client:
        store_data = await gql(client, "GetDeliveryStore", Q_GET_STORE, {"id": DELIVERY_STORE})
        menu = []
        for dp in store_data["deliveryStore"]["datePeriods"]:
            date_compact = dp["date"]
            date_iso = f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}"
            b_time = d_time = None
            for period in dp["periods"]:
                hhmm = to_hhmm(period["startTime"])
                h = int(hhmm[:2])
                if 6 <= h < 12:
                    b_time = date_compact + hhmm
                elif h >= 17:
                    d_time = date_compact + hhmm
            b_name = await get_dish_name(client, b_time, 300) if b_time else ""
            d_name = await get_dish_name(client, d_time, 500) if d_time else ""
            if not b_name and not d_name:
                continue  # 未公開の日は既存データを保持（上書きしない）
            menu.append({"date": date_iso, "b": b_name, "d": d_name})
        return menu


# ================================================================
# 1件予約
# ================================================================

async def reserve_one(client: httpx.AsyncClient, login: LoginInfo, item: ReserveItem, dry_run: bool = False) -> dict:
    is_breakfast = item.meal == "breakfast"
    try:
        pickup_time = await get_pickup_time(client, item.date, is_breakfast)
        menu_id, item_id = await get_menu_item(client, pickup_time, is_breakfast)
        cart_data = await gql(client, "UpsertCart", M_UPSERT_CART, {
            "cartInput": {
                "cartItemInputs": [{
                    "cartOptionGroupInputs": [],
                    "deliveryStoreItemId": item_id,
                    "deliveryStoreMenuId": menu_id,
                    "quantity": 1,
                }],
                "couponIds": [],
                "deliveryStoreId": DELIVERY_STORE,
                "orderMethod": "TAKE_OUT",
                "pickupTime": pickup_time,
            }
        })
        cart_id = cart_data["upsertCart"]["id"]

        if dry_run:
            return {"date": item.date, "meal": item.meal, "success": True,
                    "message": "[DRY RUN] 予約完了（実際の予約はされていません）"}

        phone = re.sub(r"[^0-9]", "", login.phone)
        await gql(client, "CreateTakeoutOrder", M_CREATE_ORDER, {
            "input": {
                "cartId": cart_id,
                "customInstructionInputs": [],
                "email": login.email,
                "guestUser": {"isPromotionPermitted": True},
                "name": login.room,
                "payType": "IN_STORE_PAYMENT",
                "phoneNumber": phone,
                "pickupTime": pickup_time,
                "siteId": SITE_ID,
            }
        })
        return {"date": item.date, "meal": item.meal, "success": True, "message": "予約完了"}
    except Exception as e:
        return {"date": item.date, "meal": item.meal, "success": False, "message": str(e)}


# ================================================================
# エンドポイント — ヘルス
# ================================================================

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}


# ================================================================
# エンドポイント — パスキー登録
# ================================================================

@app.post("/passkey/register/start")
async def passkey_register_start(req: PkRegStartReq):
    options = wa.generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=req.username.encode(),
        user_name=req.username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    challenge_id = secrets.token_hex(16)
    _pk_challenges[challenge_id] = {"challenge": options.challenge, "exp": time.time() + 300}

    return {
        "challenge_id": challenge_id,
        "challenge": bytes_to_base64url(options.challenge),
        "rp": {"id": RP_ID, "name": RP_NAME},
        "user": {
            "id": bytes_to_base64url(req.username.encode()),
            "name": req.username,
            "displayName": req.username,
        },
        "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
        "authenticatorSelection": {
            "residentKey": "required",
            "userVerification": "required",
        },
        "timeout": 60000,
        "attestation": "none",
    }


@app.post("/passkey/register/finish")
async def passkey_register_finish(req: PkRegFinishReq):
    ch = _pk_challenges.pop(req.challenge_id, None)
    if not ch or ch["exp"] < time.time():
        raise HTTPException(400, "チャレンジが無効または期限切れです")

    try:
        cred = req.credential
        verification = wa.verify_registration_response(
            credential=RegistrationCredential(
                id=cred["id"],
                raw_id=base64url_to_bytes(cred["rawId"]),
                response=AuthenticatorAttestationResponse(
                    client_data_json=base64url_to_bytes(cred["response"]["clientDataJSON"]),
                    attestation_object=base64url_to_bytes(cred["response"]["attestationObject"]),
                ),
            ),
            expected_challenge=ch["challenge"],
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
        )
    except Exception as e:
        raise HTTPException(400, f"登録検証失敗: {e}")

    await sb_post("passkey_credentials", {
        "credential_id": bytes_to_base64url(verification.credential_id),
        "user_id": req.username,
        "public_key": bytes_to_base64url(verification.credential_public_key),
        "sign_count": verification.sign_count,
    })

    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    await sb_post("passkey_sessions", {"token": token, "user_id": req.username, "expires_at": exp})

    return {"token": token, "username": req.username}


# ================================================================
# エンドポイント — パスキー認証
# ================================================================

@app.post("/passkey/login/start")
async def passkey_login_start():
    options = wa.generate_authentication_options(
        rp_id=RP_ID,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_id = secrets.token_hex(16)
    _pk_challenges[challenge_id] = {"challenge": options.challenge, "exp": time.time() + 300}

    return {
        "challenge_id": challenge_id,
        "challenge": bytes_to_base64url(options.challenge),
        "rpId": RP_ID,
        "userVerification": "required",
        "allowCredentials": [],
        "timeout": 60000,
    }


@app.post("/passkey/login/finish")
async def passkey_login_finish(req: PkLoginFinishReq):
    ch = _pk_challenges.pop(req.challenge_id, None)
    if not ch or ch["exp"] < time.time():
        raise HTTPException(400, "チャレンジが無効または期限切れです")

    cred_id = req.credential.get("id", "")
    rows = await sb_get("passkey_credentials", {"credential_id": f"eq.{cred_id}"})
    if not rows:
        raise HTTPException(400, "このパスキーは登録されていません")

    row = rows[0]
    try:
        cred = req.credential
        verification = wa.verify_authentication_response(
            credential=AuthenticationCredential(
                id=cred["id"],
                raw_id=base64url_to_bytes(cred["rawId"]),
                response=AuthenticatorAssertionResponse(
                    client_data_json=base64url_to_bytes(cred["response"]["clientDataJSON"]),
                    authenticator_data=base64url_to_bytes(cred["response"]["authenticatorData"]),
                    signature=base64url_to_bytes(cred["response"]["signature"]),
                    user_handle=base64url_to_bytes(cred["response"]["userHandle"])
                    if cred["response"].get("userHandle") else None,
                ),
            ),
            expected_challenge=ch["challenge"],
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
            credential_public_key=base64url_to_bytes(row["public_key"]),
            credential_current_sign_count=row["sign_count"],
        )
    except Exception as e:
        raise HTTPException(400, f"認証失敗: {e}")

    await sb_patch("passkey_credentials",
                   {"credential_id": f"eq.{cred_id}"},
                   {"sign_count": verification.new_sign_count})

    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    await sb_post("passkey_sessions", {"token": token, "user_id": row["user_id"], "expires_at": exp})

    return {"token": token, "username": row["user_id"]}


# ================================================================
# エンドポイント — 予約
# ================================================================

# ================================================================
# エンドポイント — 献立
# ================================================================

@app.get("/menu")
async def get_menu():
    rows = await sb_get("app_config", {"key": "eq.weekly_menu"})
    if not rows:
        return {"menu": []}
    return {"menu": json.loads(rows[0]["value"])}

@app.post("/menu")
async def set_menu(req: MenuSaveReq, authorization: str = Header(default="")):
    await verify_session(authorization)
    rows = await sb_get("app_config", {"key": "eq.weekly_menu"})
    val = json.dumps(req.menu, ensure_ascii=False)
    if rows:
        await sb_patch("app_config", {"key": "eq.weekly_menu"}, {"value": val})
    else:
        await sb_post("app_config", {"key": "weekly_menu", "value": val})
    return {"ok": True}


@app.post("/menu/auto_refresh")
async def auto_refresh_menu(authorization: str = Header(default="")):
    # Camel Order（予約サイト）側の献立をそのまま取り込む定期ジョブ用。
    # ユーザーセッションではなく専用シークレットで認証する。
    secret = os.environ.get("MENU_REFRESH_SECRET", "")
    token = authorization.replace("Bearer ", "").strip()
    if not secret or token != secret:
        raise HTTPException(401, "unauthorized")

    fetched = await fetch_weekly_menu_from_camel()

    rows = await sb_get("app_config", {"key": "eq.weekly_menu"})
    existing = json.loads(rows[0]["value"]) if rows else []
    by_date = {m["date"]: m for m in existing}
    for m in fetched:
        by_date[m["date"]] = m
    merged = sorted(by_date.values(), key=lambda m: m["date"])

    val = json.dumps(merged, ensure_ascii=False)
    if rows:
        await sb_patch("app_config", {"key": "eq.weekly_menu"}, {"value": val})
    else:
        await sb_post("app_config", {"key": "weekly_menu", "value": val})
    return {"ok": True, "updated": len(fetched)}


# ================================================================
# エンドポイント — アクセスログ
# ================================================================

class AccessLogReq(BaseModel):
    username: str = ""

@app.post("/log_access")
async def log_access(req: AccessLogReq, authorization: str = Header(default="")):
    username = req.username or None
    try:
        token = authorization.replace("Bearer ", "").strip()
        now = datetime.now(timezone.utc).isoformat()
        rows = await sb_get("passkey_sessions", {"token": f"eq.{token}", "expires_at": f"gt.{now}"})
        if rows:
            username = rows[0]["user_id"]
    except Exception:
        pass
    await sb_post("access_log", {"username": username})
    return {"ok": True}


@app.post("/reserve")
async def reserve(req: ReserveRequest, request: Request, authorization: str = Header(default="")):
    await verify_session(authorization)

    # 停止フラグチェック
    rows = await sb_get("app_config", {"key": "eq.reservation_enabled"})
    if rows and rows[0]["value"] != "true":
        raise HTTPException(503, "現在予約を停止しています。しばらくお待ちください。")

    ip = request.client.host
    now = time.time()
    day_ago = now - 86400
    _rate[ip] = [t for t in _rate[ip] if t > day_ago]
    if len(_rate[ip]) + len(req.items) > RATE_LIMIT:
        raise HTTPException(status_code=429, detail="1日の予約上限（11件）を超えました")
    _rate[ip].extend([now] * len(req.items))

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[
            reserve_one(client, req.login, item, req.dry_run) for item in req.items
        ])
    ok = sum(1 for r in results if r["success"])
    return {"ok": ok, "total": len(results), "results": list(results)}
