const AGGREGATE_DEVICE = "__total__";

const state = {
  selectedDevice: AGGREGATE_DEVICE,
  selectedMinutes: 5,
  devices: [],
  overviewPoints: [],
  socket: null,
};

const downloadRateEl = document.getElementById("download-rate");
const uploadRateEl = document.getElementById("upload-rate");
const combinedRateEl = document.getElementById("combined-rate");
const connectionStatusEl = document.getElementById("connection-status");
const deviceSelectEl = document.getElementById("device-select");
const deviceTableBodyEl = document.getElementById("device-table-body");
const healthListEl = document.getElementById("health-list");
const rangeButtonsEl = document.getElementById("range-buttons");
const deviceChartSubtitleEl = document.getElementById("device-chart-subtitle");

function bitsToHuman(bitsPerSecond) {
  if (!Number.isFinite(bitsPerSecond) || bitsPerSecond < 0) {
    return "0 bit/s";
  }
  const units = ["bit/s", "Kbit/s", "Mbit/s", "Gbit/s"];
  let value = bitsPerSecond;
  let unitIndex = 0;
  while (value >= 1000 && unitIndex < units.length - 1) {
    value /= 1000;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 2)} ${units[unitIndex]}`;
}

function formatTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleTimeString();
}

function setConnectionStatus(mode, label) {
  connectionStatusEl.className = `status-pill status-${mode}`;
  connectionStatusEl.textContent = label;
}

const overviewChart = new Chart(document.getElementById("overview-sparkline"), {
  type: "line",
  data: {
    labels: [],
    datasets: [
      {
        label: "Download",
        data: [],
        borderColor: "#35d0ba",
        backgroundColor: "rgba(53, 208, 186, 0.12)",
        fill: true,
        tension: 0.25,
        pointRadius: 0,
      },
      {
        label: "Upload",
        data: [],
        borderColor: "#ffb347",
        backgroundColor: "rgba(255, 179, 71, 0.08)",
        fill: true,
        tension: 0.25,
        pointRadius: 0,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: { legend: { labels: { color: "#e8eefc" } } },
    scales: {
      x: {
        ticks: { color: "#93a4c3", maxTicksLimit: 6 },
        grid: { color: "rgba(255,255,255,0.05)" },
      },
      y: {
        ticks: {
          color: "#93a4c3",
          callback: (value) => bitsToHuman(value),
        },
        grid: { color: "rgba(255,255,255,0.05)" },
      },
    },
  },
});

const deviceChart = new Chart(document.getElementById("device-chart"), {
  type: "line",
  data: {
    labels: [],
    datasets: [
      {
        label: "Download",
        data: [],
        borderColor: "#35d0ba",
        backgroundColor: "rgba(53, 208, 186, 0.12)",
        fill: true,
        tension: 0.25,
        pointRadius: 0,
      },
      {
        label: "Upload",
        data: [],
        borderColor: "#ffb347",
        backgroundColor: "rgba(255, 179, 71, 0.08)",
        fill: true,
        tension: 0.25,
        pointRadius: 0,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: { legend: { labels: { color: "#e8eefc" } } },
    scales: {
      x: {
        ticks: { color: "#93a4c3", maxTicksLimit: 8 },
        grid: { color: "rgba(255,255,255,0.05)" },
      },
      y: {
        ticks: {
          color: "#93a4c3",
          callback: (value) => bitsToHuman(value),
        },
        grid: { color: "rgba(255,255,255,0.05)" },
      },
    },
  },
});

function updateOverviewRates(latest) {
  if (!latest) {
    return;
  }
  const recv = latest.recv_bps || 0;
  const sent = latest.sent_bps || 0;
  downloadRateEl.textContent = bitsToHuman(recv);
  uploadRateEl.textContent = bitsToHuman(sent);
  combinedRateEl.textContent = bitsToHuman(recv + sent);
}

function updateOverviewChart(history) {
  state.overviewPoints = history || [];
  overviewChart.data.labels = state.overviewPoints.map((point) =>
    formatTime(point.timestamp)
  );
  overviewChart.data.datasets[0].data = state.overviewPoints.map(
    (point) => point.recv_bps
  );
  overviewChart.data.datasets[1].data = state.overviewPoints.map(
    (point) => point.sent_bps
  );
  overviewChart.update();
}

function renderDeviceOptions() {
  const previous = state.selectedDevice;
  deviceSelectEl.innerHTML = "";
  const aggregateOption = document.createElement("option");
  aggregateOption.value = AGGREGATE_DEVICE;
  aggregateOption.textContent = "Household total";
  deviceSelectEl.appendChild(aggregateOption);

  for (const device of state.devices) {
    const option = document.createElement("option");
    option.value = device.device_id;
    option.textContent = device.name || device.device_id;
    deviceSelectEl.appendChild(option);
  }

  const values = [AGGREGATE_DEVICE, ...state.devices.map((d) => d.device_id)];
  state.selectedDevice = values.includes(previous) ? previous : AGGREGATE_DEVICE;
  deviceSelectEl.value = state.selectedDevice;
}

function renderDeviceTable(snapshots, rates) {
  const rateById = new Map((rates || []).map((rate) => [rate.device_id, rate]));
  if (!snapshots || snapshots.length === 0) {
    deviceTableBodyEl.innerHTML =
      '<tr><td colspan="7" class="empty-row">No household devices yet.</td></tr>';
    return;
  }

  deviceTableBodyEl.innerHTML = snapshots
    .map((device) => {
      const rate = rateById.get(device.device_id) || {};
      const status = device.is_online ? "online" : "offline";
      const badge = device.is_online ? "up" : "down";
      return `
        <tr>
          <td>${device.name || device.device_id}</td>
          <td><span class="badge badge-${badge}">${status}</span></td>
          <td>${device.connection || "unknown"}</td>
          <td>${device.ip || "—"}</td>
          <td>${device.mac || "—"}</td>
          <td>${bitsToHuman(rate.recv_bps || 0)}</td>
          <td>${bitsToHuman(rate.sent_bps || 0)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderHealth(events) {
  if (!events || events.length === 0) {
    healthListEl.innerHTML = '<li class="empty-row">No health events yet.</li>';
    return;
  }
  healthListEl.innerHTML = events
    .map(
      (event) => `
      <li>
        <strong>${event.event_type}</strong>
        <span>${event.message}</span>
        <small>${formatTime(event.timestamp)}</small>
      </li>
    `
    )
    .join("");
}

async function refreshOverview() {
  const response = await fetch(`/api/overview?minutes=${state.selectedMinutes}`);
  const payload = await response.json();
  updateOverviewRates(payload.latest);
  updateOverviewChart(payload.history);
}

async function refreshDevices() {
  const response = await fetch("/api/devices");
  const payload = await response.json();
  state.devices = payload.snapshots || [];
  renderDeviceOptions();
  renderDeviceTable(payload.snapshots, payload.rates);
}

async function refreshDeviceChart() {
  const response = await fetch(
    `/api/history?device=${encodeURIComponent(state.selectedDevice)}&minutes=${state.selectedMinutes}`
  );
  const payload = await response.json();
  const samples = payload.samples || [];
  const label =
    state.selectedDevice === AGGREGATE_DEVICE
      ? "Household total"
      : state.devices.find((d) => d.device_id === state.selectedDevice)?.name ||
        state.selectedDevice;
  deviceChartSubtitleEl.textContent = `${label} · last ${state.selectedMinutes} min`;
  deviceChart.data.labels = samples.map((point) => formatTime(point.timestamp));
  deviceChart.data.datasets[0].data = samples.map((point) => point.recv_bps);
  deviceChart.data.datasets[1].data = samples.map((point) => point.sent_bps);
  deviceChart.update();
}

async function refreshHealth() {
  const response = await fetch("/api/health?limit=50");
  const payload = await response.json();
  renderHealth(payload.events);
}

async function refreshAll() {
  await Promise.all([
    refreshOverview(),
    refreshDevices(),
    refreshDeviceChart(),
    refreshHealth(),
  ]);
}

function connectSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/live`);
  state.socket = socket;
  setConnectionStatus("connecting", "Connecting…");

  socket.addEventListener("open", () => {
    setConnectionStatus("live", "Live");
  });

  socket.addEventListener("close", () => {
    setConnectionStatus("offline", "Offline");
    window.setTimeout(connectSocket, 2000);
  });

  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "hello" && payload.latest) {
      updateOverviewRates(payload.latest);
      return;
    }
    if (payload.type === "sample") {
      updateOverviewRates(payload);
      refreshDevices();
      refreshHealth();
      if (state.selectedDevice === AGGREGATE_DEVICE) {
        state.overviewPoints.push({
          timestamp: payload.timestamp,
          recv_bps: payload.recv_bps,
          sent_bps: payload.sent_bps,
        });
        if (state.overviewPoints.length > 360) {
          state.overviewPoints.shift();
        }
        updateOverviewChart(state.overviewPoints);
      }
      refreshDeviceChart();
    }
  });
}

deviceSelectEl.addEventListener("change", () => {
  state.selectedDevice = deviceSelectEl.value;
  refreshDeviceChart();
});

rangeButtonsEl.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-minutes]");
  if (!button) {
    return;
  }
  for (const child of rangeButtonsEl.querySelectorAll("button")) {
    child.classList.toggle("active", child === button);
  }
  state.selectedMinutes = Number(button.dataset.minutes);
  refreshOverview();
  refreshDeviceChart();
});

refreshAll()
  .then(connectSocket)
  .catch(() => {
    setConnectionStatus("offline", "Offline");
  });
