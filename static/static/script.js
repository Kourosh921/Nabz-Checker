
const pricesBox = document.getElementById("prices");
const statusBox = document.getElementById("status");
const updatedBox = document.getElementById("updated");
const messageBox = document.getElementById("message");
const refreshButton = document.getElementById("refresh");

function formatPrice(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "—";
  }
  return Number(value).toLocaleString("en-US", {
    maximumFractionDigits: 2
  }) + " تومان";
}

function renderPrices(data) {
  const channel = data.channel || {};
  const tgju = data.tgju || {};
  const names = [...new Set([
    ...Object.keys(channel),
    ...Object.keys(tgju)
  ])];

  if (!names.length) {
    pricesBox.innerHTML =
      '<div class="loading">هنوز اطلاعات قیمتی دریافت نشده است.</div>';
    return;
  }

  pricesBox.innerHTML = names.map(name => {
    const market = Number(tgju[name]);
    const channelPrice = Number(channel[name]);

    let difference = "—";
    let differenceClass = "neutral";

    if (Number.isFinite(channelPrice) && Number.isFinite(market) && market > 0) {
      const amount = channelPrice - market;
      const percent = amount / market * 100;

      difference = `${formatPrice(amount)} (${percent.toFixed(2)}٪)`;

      if (amount > 0) differenceClass = "positive";
      if (amount < 0) differenceClass = "negative";
    }

    return `
      <div class="price-row">
        <strong>${escapeHTML(name)}</strong>
        <span>${formatPrice(channel[name])}</span>
        <span>${formatPrice(tgju[name])}</span>
        <strong class="${differenceClass}">${difference}</strong>
      </div>
    `;
  }).join("");
}

function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[char]);
}

async function loadData() {
  refreshButton.disabled = true;
  statusBox.textContent = "در حال دریافت اطلاعات...";

  try {
    const response = await fetch("/api/state", {
      cache: "no-store"
    });

    if (!response.ok) throw new Error("خطا در دریافت اطلاعات");

    const data = await response.json();

    renderPrices(data);

    statusBox.textContent = data.status || "اطلاعات دریافت شد";
    updatedBox.textContent = data.updated_at || "—";
    messageBox.textContent =
      data.last_message || "هنوز پیام جدیدی دریافت نشده است.";

  } catch (error) {
    statusBox.textContent = "اتصال ناموفق";
    pricesBox.innerHTML =
      '<div class="loading">ارتباط با سرور برقرار نشد.</div>';
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener("click", loadData);

loadData();
setInterval(loadData, 30000);
