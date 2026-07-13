"""
食堂まとめ予約 - FastAPI バックエンド
GraphQL API を直接呼び出して食事を予約します（Playwright不使用）。
"""
import asyncio
import re
from typing import Literal

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="食堂まとめ予約 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

GRAPHQL         = "https://shonanfujisawa-international-dormitory.mo-order.com/api/graphql"
DELIVERY_STORE  = "2db98ea3-f9fb-4b3b-86cc-e18677b01491"
SITE_ID         = "d1161f9d-ab82-41ea-ad43-bf047d86b731"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Origin": "https://shonanfujisawa-international-dormitory.mo-order.com",
    "Referer": "https://shonanfujisawa-international-dormitory.mo-order.com/",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
}


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

M_UPSERT_CART = """
mutation UpsertCart($cartInput: CartInput!) {
  upsertCart(input: $cartInput) { id }
}"""

M_CREATE_ORDER = """
mutation CreateTakeoutOrder($input: TakeOrderInput!) {
  createTakeoutOrder(input: $input) { id }
}"""


# ================================================================
# ヘルパー
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
    """'18:30' or '1830' or '18:30:00' → '1830'"""
    return re.sub(r"[^0-9]", "", t)[:4]


async def get_pickup_time(client: httpx.AsyncClient, date_str: str, is_breakfast: bool) -> str:
    """日付と朝/夕から pickupTime (YYYYMMDDHHMM) を返す"""
    data = await gql(client, "GetDeliveryStore", Q_GET_STORE, {"id": DELIVERY_STORE})
    date_compact = date_str.replace("-", "")

    for dp in data["deliveryStore"]["datePeriods"]:
        dp_date = dp["date"].replace("-", "")
        if dp_date != date_compact:
            continue
        for period in dp["periods"]:
            hhmm = to_hhmm(period["startTime"])
            h = int(hhmm[:2])
            if is_breakfast and 6 <= h < 12:
                return date_compact + hhmm
            if not is_breakfast and h >= 17:
                return date_compact + hhmm

    # フォールバック（取得できなかった場合）
    return date_compact + ("0800" if is_breakfast else "1830")


async def get_menu_item(client: httpx.AsyncClient, pickup_time: str, is_breakfast: bool):
    """メニューから対象アイテムのIDを返す"""
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
# 1件予約
# ================================================================

async def reserve_one(client: httpx.AsyncClient, login: LoginInfo, item: ReserveItem) -> dict:
    is_breakfast = item.meal == "breakfast"
    try:
        # 1. pickupTime を取得
        pickup_time = await get_pickup_time(client, item.date, is_breakfast)

        # 2. メニューアイテム ID を取得
        menu_id, item_id = await get_menu_item(client, pickup_time, is_breakfast)

        # 3. カートに追加
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

        # 4. 注文確定
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
# エンドポイント
# ================================================================

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}


@app.post("/reserve")
async def reserve(req: ReserveRequest):
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[
            reserve_one(client, req.login, item) for item in req.items
        ])
    ok = sum(1 for r in results if r["success"])
    return {"ok": ok, "total": len(results), "results": list(results)}
