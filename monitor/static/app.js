const AGGREGATE_INTERFACE = "__total__";

const state = {
  selectedHost: "local",
  selectedInterface: null,
  selectedMinutes: 5,
  hosts: [],
  interfaces: [],
  overviewPoints: [],
  socket: null,
};

const downloadRateEl = document.getElementById("download-rate");
const uploadRateEl = document.getElementById("upload-rate");
const combinedRateEl = document.getElementById("combined-rate");
const connectionStatusEl = document.getElementById("connection-status");
const alertStatusEl = document.getElementById("alert-status");
const hostSelectEl = document.getElementById("host-select");
const interfaceSelectEl = document.getElementById("interface-select");
const interfaceTableBodyEl = document.getElementById("interface-table-body");
const healthListEl = document.getElementById("health-list");
const rangeButtonsEl = document.getElementById("range-buttons");
const interfaceChartSubtitleEl = document.getElementById("interface-chart-subtitle");

function hostQuery() {
  return `host=${encodeURIComponent(state.selectedHost)}`;
}

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

function bytesToHuman(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "0 B";
  }
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleTimeString();
}

function setConnectionStatus(mode, label) {
  connectionStatusEl.className = `status-pill status-${mode}`;
  connectionStatusEl.textContent = label;
}

function setAlertStatus(mode, label) {
  alertStatusEl.hidden = false;
  alertStatusEl.className = `status-pill status-alerts-${mode}`;
  alertStatusEl.textContent = label;
}

async function refreshAlertStatus() {
  const response = await fetch("/api/alerts/status");
  const status = await response.json();
  if (!status.bandwidth_enabled && !status.webhook_configured) {
    alertStatusEl.hidden = true;
    return;
  }

  const parts = [];
  if (status.bandwidth_enabled) {
    parts.push(`${status.bandwidth_mbps_threshold} Mbps`);
  }
  if (status.webhook_configured) {
    parts.push("webhook");
  }
  setAlertStatus("armed", `Alerts: ${parts.join(", ")}`);
}

function markAlertFired(count) {
  setAlertStatus("fired", `${count} alert${count === 1 ? "" : "s"} fired`);
  window.setTimeout(refreshAlertStatus, 5000);
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
    plugins: {
      legend: { labels: { color: "#e8eefc" } },
    },
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

const interfaceChart = new Chart(document.getElementById("interface-chart"), {
  type: "line",
  data: {
    labels: [],
    datasets: [
      {
        label: "Download",
        data: [],
        borderColor: "#35d0ba",
        tension: 0.2,
        pointRadius: 0,
      },
      {
        label: "Upload",
        data: [],
        borderColor: "#ffb347",
        tension: 0.2,
        pointRadius: 0,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: {
      legend: { labels: { color: "#e8eefc" } },
    },
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

function updateOverviewRates(recvBps, sentBps) {
  downloadRateEl.textContent = bitsToHuman(recvBps);
  uploadRateEl.textContent = bitsToHuman(sentBps);
  combinedRateEl.textContent = bitsToHuman(recvBps + sentBps);
}

function appendOverviewPoint(timestamp, recvBps, sentBps) {
  const label = formatTime(timestamp);
  overviewChart.data.labels.push(label);
  overviewChart.data.datasets[0].data.push(recvBps);
  overviewChart.data.datasets[1].data.push(sentBps);
  state.overviewPoints.push({ timestamp, recvBps, sentBps });

  const maxPoints = 120;
  while (overviewChart.data.labels.length > maxPoints) {
    overviewChart.data.labels.shift();
    overviewChart.data.datasets.forEach((dataset) => dataset.data.shift());
    state.overviewPoints.shift();
  }
  overviewChart.update("none");
}

function clearOverviewChart() {
  state.overviewPoints = [];
  overviewChart.data.labels = [];
  overviewChart.data.datasets.forEach((dataset) => {
    dataset.data = [];
  });
  overviewChart.update("none");
  updateOverviewRates(0, 0);
}

function renderInterfaceOptions(interfaces) {
  const names = interfaces
    .map((item) => item.name)
    .filter((name) => name !== AGGREGATE_INTERFACE)
    .sort();

  if (names.length === 0) {
    interfaceSelectEl.innerHTML = '<option value="">No interfaces</option>';
    state.selectedInterface = null;
    return;
  }

  if (!state.selectedInterface || !names.includes(state.selectedInterface)) {
    state.selectedInterface = names[0];
  }

  interfaceSelectEl.innerHTML = names
    .map(
      (name) =>
        `<option value="${name}"${
          name === state.selectedInterface ? " selected" : ""
        }>${name}</option>`,
    )
    .join("");
}

function renderInterfaceTable(snapshots) {
  if (!snapshots.length) {
    interfaceTableBodyEl.innerHTML =
      '<tr><td colspan="7" class="empty-row">No interface data yet.</td></tr>';
    return;
  }

  interfaceTableBodyEl.innerHTML = snapshots
    .map((item) => {
      const totalErrors = item.errin + item.errout;
      const totalDrops = item.dropin + item.dropout;
      return `
        <tr>
          <td>${item.name}</td>
          <td>
            <span class="badge ${item.is_up ? "badge-up" : "badge-down"}">
              ${item.is_up ? "Up" : "Down"}
            </span>
          </td>
          <td>${item.speed_mbps} Mbps</td>
          <td>${bytesToHuman(item.bytes_recv)}</td>
          <td>${bytesToHuman(item.bytes_sent)}</td>
          <td>${totalErrors}</td>
          <td>${totalDrops}</td>
        </tr>
      `;
    })
    .join("");
}

function renderHealthEvents(events) {
  if (!events.length) {
    healthListEl.innerHTML = '<li class="empty-row">No health events yet.</li>';
    return;
  }

  healthListEl.innerHTML = events
    .map(
      (event) => `
        <li class="health-item severity-${event.severity}">
          <time>${formatTime(event.timestamp)}</time>
          <strong>${event.interface} · ${event.event_type.replaceAll("_", " ")}</strong>
          <span>${event.message}</span>
        </li>
      `,
    )
    .join("");
}

function prependHealthEvents(events) {
  if (!events.length) {
    return;
  }
  const existing = healthListEl.querySelector(".empty-row");
  if (existing) {
    healthListEl.innerHTML = "";
  }
  for (const event of events.slice().reverse()) {
    const item = document.createElement("li");
    item.className = `health-item severity-${event.severity}`;
    item.innerHTML = `
      <time>${formatTime(event.timestamp)}</time>
      <strong>${event.interface} · ${event.event_type.replaceAll("_", " ")}</strong>
      <span>${event.message}</span>
    `;
    healthListEl.prepend(item);
  }
  while (healthListEl.children.length > 30) {
    healthListEl.removeChild(healthListEl.lastChild);
  }
}

async function refreshHosts() {
  const response = await fetch("/api/hosts");
  const data = await response.json();
  state.hosts = data.hosts || [];
  hostSelectEl.innerHTML = "";
  for (const host of state.hosts) {
    const option = document.createElement("option");
    option.value = host.host_id;
    const badge = host.online ? "" : " (offline)";
    option.textContent = `${host.host_id}${badge}`;
    hostSelectEl.appendChild(option);
  }
  if (!state.hosts.some((h) => h.host_id === state.selectedHost)) {
    const local = state.hosts.find((h) => h.host_id === "local");
    state.selectedHost = local ? local.host_id : (state.hosts[0]?.host_id || "local");
  }
  hostSelectEl.value = state.selectedHost;
}

async function fetchOverviewHistory() {
  const response = await fetch(`/api/overview?minutes=5&${hostQuery()}`);
  const payload = await response.json();
  const history = payload.history || [];

  overviewChart.data.labels = history.map((item) => formatTime(item.timestamp));
  overviewChart.data.datasets[0].data = history.map((item) => item.recv_bps);
  overviewChart.data.datasets[1].data = history.map((item) => item.sent_bps);
  state.overviewPoints = history.map((item) => ({
    timestamp: item.timestamp,
    recvBps: item.recv_bps,
    sentBps: item.sent_bps,
  }));
  overviewChart.update("none");

  if (payload.latest) {
    updateOverviewRates(payload.latest.recv_bps, payload.latest.sent_bps);
  }

  if (payload.interfaces?.length) {
    renderInterfaceOptions(payload.interfaces);
  }
}

async function fetchInterfaceHistory() {
  if (!state.selectedInterface) {
    interfaceChart.data.labels = [];
    interfaceChart.data.datasets.forEach((dataset) => {
      dataset.data = [];
    });
    interfaceChart.update("none");
    return;
  }

  const response = await fetch(
    `/api/history?interface=${encodeURIComponent(state.selectedInterface)}&minutes=${state.selectedMinutes}&${hostQuery()}`,
  );
  const payload = await response.json();
  const samples = payload.samples || [];

  interfaceChartSubtitleEl.textContent = `${state.selectedInterface} over the last ${state.selectedMinutes} minutes`;
  interfaceChart.data.labels = samples.map((item) => formatTime(item.timestamp));
  interfaceChart.data.datasets[0].data = samples.map((item) => item.recv_bps);
  interfaceChart.data.datasets[1].data = samples.map((item) => item.sent_bps);
  interfaceChart.update("none");
}

async function refreshTables() {
  const [interfacesResponse, healthResponse] = await Promise.all([
    fetch(`/api/interfaces?${hostQuery()}`),
    fetch(`/api/health?limit=30&${hostQuery()}`),
  ]);

  const interfacesPayload = await interfacesResponse.json();
  const healthPayload = await healthResponse.json();

  renderInterfaceTable(interfacesPayload.snapshots || []);
  renderHealthEvents(healthPayload.events || []);

  const rates = interfacesPayload.rates || [];
  if (rates.length) {
    renderInterfaceOptions(rates);
  }
}

async function refreshDashboard() {
  clearOverviewChart();
  await Promise.all([fetchOverviewHistory(), refreshTables()]);
  await fetchInterfaceHistory();
}

function handleLiveSample(payload) {
  updateOverviewRates(payload.recv_bps, payload.sent_bps);
  appendOverviewPoint(payload.timestamp, payload.recv_bps, payload.sent_bps);

  if (payload.snapshots) {
    renderInterfaceTable(payload.snapshots);
    renderInterfaceOptions(payload.interfaces || payload.snapshots);
  }

  if (payload.health?.length) {
    prependHealthEvents(payload.health);
  }

  if (payload.alerts?.length) {
    markAlertFired(payload.alerts.length);
  }

  if (
    state.selectedInterface &&
    payload.interfaces?.some((item) => item.name === state.selectedInterface)
  ) {
    const current = payload.interfaces.find(
      (item) => item.name === state.selectedInterface,
    );
    if (current && interfaceChart.data.labels.length > 0) {
      interfaceChart.data.labels.push(formatTime(current.timestamp));
      interfaceChart.data.datasets[0].data.push(current.recv_bps);
      interfaceChart.data.datasets[1].data.push(current.sent_bps);
      while (interfaceChart.data.labels.length > 300) {
        interfaceChart.data.labels.shift();
        interfaceChart.data.datasets.forEach((dataset) => dataset.data.shift());
      }
      interfaceChart.update("none");
    }
  }
}

function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  state.socket = new WebSocket(`${protocol}://${window.location.host}/ws/live`);

  state.socket.addEventListener("open", () => {
    setConnectionStatus("live", "Live");
  });

  state.socket.addEventListener("close", () => {
    setConnectionStatus("offline", "Reconnecting…");
    window.setTimeout(connectWebSocket, 2000);
  });

  state.socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.host_id && message.host_id !== state.selectedHost) {
      return;
    }
    if (message.type === "sample") {
      handleLiveSample(message);
    }
  });
}

hostSelectEl.addEventListener("change", async () => {
  state.selectedHost = hostSelectEl.value;
  state.overviewPoints = [];
  state.selectedInterface = null;
  await refreshDashboard();
});

interfaceSelectEl.addEventListener("change", () => {
  state.selectedInterface = interfaceSelectEl.value || null;
  fetchInterfaceHistory();
});

rangeButtonsEl.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-minutes]");
  if (!button) {
    return;
  }
  state.selectedMinutes = Number(button.dataset.minutes);
  rangeButtonsEl.querySelectorAll("button").forEach((item) => {
    item.classList.toggle("active", item === button);
  });
  fetchInterfaceHistory();
});

async function bootstrap() {
  await refreshHosts();
  await Promise.all([
    fetchOverviewHistory(),
    fetchInterfaceHistory(),
    refreshTables(),
    refreshAlertStatus(),
  ]);
  connectWebSocket();
  window.setInterval(refreshTables, 15000);
  window.setInterval(fetchInterfaceHistory, 30000);
  window.setInterval(refreshHosts, 30000);
}

bootstrap();
