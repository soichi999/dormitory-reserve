(function () {
  // 二重起動防止
  if (document.getElementById('shokudo-bm')) {
    document.getElementById('shokudo-bm').style.display = 'flex';
    return;
  }

  const GQL = '/api/graphql';
  const STORE = '2db98ea3-f9fb-4b3b-86cc-e18677b01491';
  const SITE  = 'd1161f9d-ab82-41ea-ad43-bf047d86b731';
  const WEEK  = ['日','月','火','水','木','金','土'];
  const CKEY  = 'shokudo_creds';
  const HKEY  = 'shokudo_history';

  // ── ヘルパー ──
  const gql = (op, query, vars) =>
    fetch(GQL, { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ operationName:op, query, variables:vars }) })
    .then(r => r.json()).then(d => {
      if (d.errors) throw new Error(d.errors[0].message);
      return d.data;
    });

  function getHistory() { return JSON.parse(localStorage.getItem(HKEY)||'{}'); }
  function saveHistory(date, meal) {
    const h = getHistory(); h[`${date}_${meal}`] = true;
    localStorage.setItem(HKEY, JSON.stringify(h));
  }
  function getCreds() { return JSON.parse(localStorage.getItem(CKEY)||'{}'); }
  function saveCreds(e, p, r) { localStorage.setItem(CKEY, JSON.stringify({e,p,r})); }

  function getdays() {
    const today = new Date(); today.setHours(0,0,0,0);
    const dow = today.getDay();
    const end = new Date(today);
    if (dow >= 1 && dow <= 3) end.setDate(today.getDate() + (5-dow));
    else { const m = dow===0?1:dow===6?2:8-dow; end.setDate(today.getDate()+m+4); }
    const days=[], d=new Date(today); d.setDate(d.getDate()+1);
    while (d<=end) { if(d.getDay()>=1&&d.getDay()<=5) days.push(new Date(d)); d.setDate(d.getDate()+1); }
    return days;
  }
  function pad(n) { return String(n).padStart(2,'0'); }
  function toKey(d) { return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`; }
  function canB(d) { const dl=new Date(d); dl.setDate(dl.getDate()-1); dl.setHours(9,0,0,0); return Date.now()<dl.getTime(); }
  function canD(d) { const dl=new Date(d); dl.setHours(9,0,0,0); return Date.now()<dl.getTime(); }

  // ── UI ──
  const overlay = document.createElement('div');
  overlay.id = 'shokudo-bm';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:999999;display:flex;align-items:center;justify-content:center;font-family:-apple-system,sans-serif';

  const creds = getCreds();
  const history = getHistory();
  const days = getdays();

  let rows = '';
  days.forEach(d => {
    const key = toKey(d);
    const label = `${d.getMonth()+1}/${d.getDate()}（${WEEK[d.getDay()]}）`;
    const doneB = history[`${key}_breakfast`];
    const doneD = history[`${key}_dinner`];
    const cB = canB(d), cD = canD(d);
    const cbB = doneB ? '予約済' : cB ? `<input type="checkbox" class="bm-cb" data-date="${key}" data-meal="breakfast" style="width:18px;height:18px;accent-color:#2563eb">` : '締切';
    const cbD = doneD ? '予約済' : cD ? `<input type="checkbox" class="bm-cb" data-date="${key}" data-meal="dinner" style="width:18px;height:18px;accent-color:#2563eb">` : '締切';
    rows += `<tr><td style="padding:7px 6px;font-size:13px;color:#1f2937">${label}</td><td style="padding:7px 6px;text-align:center;font-size:11px;color:#9ca3af">${cbB}</td><td style="padding:7px 6px;text-align:center;font-size:11px;color:#9ca3af">${cbD}</td></tr>`;
  });

  overlay.innerHTML = `
  <div style="background:#fff;border-radius:16px;width:92%;max-width:400px;max-height:90vh;overflow-y:auto;padding:20px;box-shadow:0 20px 60px rgba(0,0,0,.3)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <span style="font-size:17px;font-weight:700;color:#111827">🍱 食堂まとめ予約</span>
      <button id="bm-close" style="background:none;border:none;font-size:20px;cursor:pointer;color:#9ca3af">✕</button>
    </div>
    <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px">
      <input id="bm-email" placeholder="メールアドレス" value="${creds.e||''}" style="border:1.5px solid #e5e7eb;border-radius:8px;padding:9px 12px;font-size:14px;outline:none">
      <input id="bm-phone" placeholder="電話番号" value="${creds.p||''}" style="border:1.5px solid #e5e7eb;border-radius:8px;padding:9px 12px;font-size:14px;outline:none">
      <input id="bm-room"  placeholder="号室番号＋氏名（例: 101田中太郎）" value="${creds.r||''}" style="border:1.5px solid #e5e7eb;border-radius:8px;padding:9px 12px;font-size:14px;outline:none">
    </div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:12px">
      <thead><tr style="background:#f3f4f6">
        <th style="padding:8px 6px;text-align:left;font-size:12px;font-weight:700;color:#374151">日付</th>
        <th style="padding:8px 6px;text-align:center;font-size:12px;font-weight:700;color:#374151">朝食<br><span style="font-weight:400;color:#9ca3af">¥300</span></th>
        <th style="padding:8px 6px;text-align:center;font-size:12px;font-weight:700;color:#374151">夕食<br><span style="font-weight:400;color:#9ca3af">¥500</span></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div id="bm-total" style="text-align:center;font-size:13px;color:#6b7280;margin-bottom:10px">食事を選択してください</div>
    <button id="bm-btn" disabled style="width:100%;padding:13px;background:#93c5fd;color:#fff;border:none;border-radius:12px;font-size:15px;font-weight:700;cursor:not-allowed">予約する</button>
    <div id="bm-results" style="margin-top:12px;display:flex;flex-direction:column;gap:6px"></div>
  </div>`;

  document.body.appendChild(overlay);

  function updateTotal() {
    const cbs = [...overlay.querySelectorAll('.bm-cb:checked')];
    const nb = cbs.filter(c=>c.dataset.meal==='breakfast').length;
    const nd = cbs.filter(c=>c.dataset.meal==='dinner').length;
    const total = nb+nd;
    const email = overlay.querySelector('#bm-email').value.trim();
    const phone = overlay.querySelector('#bm-phone').value.trim();
    const room  = overlay.querySelector('#bm-room').value.trim();
    const ok = total>0 && email && phone && room;
    const btn = overlay.querySelector('#bm-btn');
    btn.disabled = !ok;
    btn.style.background = ok ? '#2563eb' : '#93c5fd';
    btn.style.cursor = ok ? 'pointer' : 'not-allowed';
    overlay.querySelector('#bm-total').textContent = total===0
      ? '食事を選択してください'
      : `朝食 ${nb}回 ＋ 夕食 ${nd}回 ＝ ${total}件（¥${(nb*300+nd*500).toLocaleString()}）`;
  }

  overlay.querySelector('#bm-close').onclick = () => overlay.style.display='none';
  overlay.addEventListener('change', updateTotal);
  ['bm-email','bm-phone','bm-room'].forEach(id => overlay.querySelector('#'+id).addEventListener('input', updateTotal));

  overlay.querySelector('#bm-btn').onclick = async () => {
    const email = overlay.querySelector('#bm-email').value.trim();
    const phone = overlay.querySelector('#bm-phone').value.trim().replace(/[^0-9]/g,'');
    const room  = overlay.querySelector('#bm-room').value.trim();
    saveCreds(email, overlay.querySelector('#bm-phone').value.trim(), room);

    const items = [...overlay.querySelectorAll('.bm-cb:checked')]
      .map(c=>({date:c.dataset.date, meal:c.dataset.meal}));

    const btn = overlay.querySelector('#bm-btn');
    btn.disabled = true; btn.textContent = '予約中...';
    const rl = overlay.querySelector('#bm-results'); rl.innerHTML='';

    const Q_STORE = `query GetDeliveryStore($id:UUID!){deliveryStore(id:$id){datePeriods{date periods{startTime}}}}`;
    const Q_MENU  = `query GetStoreMenus($deliveryStoreId:UUID!,$pickupTime:String!,$orderMethods:[OrderMethod!]!){deliveryStoreMenus(deliveryStoreId:$deliveryStoreId,pickupTime:$pickupTime){id deliveryStoreCategories{deliveryStoreItems(pickupTime:$pickupTime,orderMethods:$orderMethods){id taxIncludedTakeoutPrice}}}}`;
    const M_CART  = `mutation UpsertCart($cartInput:CartInput!){upsertCart(input:$cartInput){id}}`;
    const M_ORDER = `mutation CreateTakeoutOrder($input:TakeOrderInput!){createTakeoutOrder(input:$input){id}}`;

    const storeData = await gql('GetDeliveryStore', Q_STORE, {id:STORE});

    for (const item of items) {
      const isB = item.meal==='breakfast';
      const dc = item.date.replace(/-/g,'');
      let pt = dc+(isB?'0800':'1830');
      for (const dp of storeData.deliveryStore.datePeriods) {
        if (dp.date.replace(/-/g,'')!==dc) continue;
        for (const p of dp.periods) {
          const hhmm = p.startTime.replace(/[^0-9]/g,'').slice(0,4);
          const h = parseInt(hhmm.slice(0,2));
          if (isB&&h>=6&&h<12){pt=dc+hhmm;break;}
          if (!isB&&h>=17){pt=dc+hhmm;break;}
        }
      }

      const li = document.createElement('div');
      li.style.cssText='font-size:13px;padding:4px 0';
      const d=new Date(item.date+'T00:00:00');
      const label=`${d.getMonth()+1}/${d.getDate()}（${WEEK[d.getDay()]}）${isB?'朝食':'夕食'}`;
      try {
        const menus = await gql('GetStoreMenus', Q_MENU, {deliveryStoreId:STORE,pickupTime:pt,orderMethods:['TAKE_OUT']});
        const price = isB?300:500;
        let menuId,itemId;
        for (const m of menus.deliveryStoreMenus)
          for (const cat of m.deliveryStoreCategories)
            for (const si of cat.deliveryStoreItems)
              if (si.taxIncludedTakeoutPrice===price){menuId=m.id;itemId=si.id;}

        if (!itemId) throw new Error('メニューが見つかりません');

        const cart = await gql('UpsertCart', M_CART, {cartInput:{
          cartItemInputs:[{cartOptionGroupInputs:[],deliveryStoreItemId:itemId,deliveryStoreMenuId:menuId,quantity:1}],
          couponIds:[],deliveryStoreId:STORE,orderMethod:'TAKE_OUT',pickupTime:pt
        }});

        await gql('CreateTakeoutOrder', M_ORDER, {input:{
          cartId:cart.upsertCart.id,customInstructionInputs:[],email,
          guestUser:{isPromotionPermitted:true},name:room,payType:'IN_STORE_PAYMENT',
          phoneNumber:phone,pickupTime:pt,siteId:SITE
        }});

        saveHistory(item.date, item.meal);
        const cb = overlay.querySelector(`.bm-cb[data-date="${item.date}"][data-meal="${item.meal}"]`);
        if (cb) cb.parentElement.innerHTML='<span style="font-size:11px;color:#9ca3af">予約済</span>';
        li.style.color='#059669'; li.textContent=`✅ ${label}　予約完了`;
      } catch(e) {
        li.style.color='#dc2626'; li.textContent=`❌ ${label}　失敗: ${e.message}`;
      }
      rl.appendChild(li);
    }

    btn.disabled=false; btn.textContent='予約する'; btn.style.background='#2563eb'; btn.style.cursor='pointer';
    updateTotal();
  };
})();
