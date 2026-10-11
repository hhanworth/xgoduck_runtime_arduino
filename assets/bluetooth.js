/* Uno Q Linux BlueZ controller: API-only, no browser Bluetooth permission. */
(() => {
  const $ = id => document.getElementById(id);
  const status = $('bt-status'), list = $('bt-devices');
  const scan = $('bt-scan'), connection = $('bt-connection');
  let pending = false, loading = false, lastSignature = '';

  async function api(method, path, data) {
    const r = await fetch('/api/bluetooth/' + path, {
      method, cache: 'no-store',
      headers: {'Content-Type': 'application/json'},
      ...(data === undefined ? {} : {body: JSON.stringify(data)})
    });
    const result = await r.json();
    if (!r.ok) throw new Error(result.detail || 'Bluetooth API error');
    return result;
  }
  async function execute(path, payload) {
    if (pending) return;
    pending = true;
    try {
      await api('POST', path, payload);
      await refresh();
    } catch (error) {
      status.textContent = '蓝牙操作未提交：' + error.message;
    } finally {
      pending = false;
    }
  }
  scan.addEventListener('click', () => execute('scan', {}));

  function deviceRow(device, saved, busy, online) {
    const row = document.createElement('div');
    row.className = 'bt-device';
    const info = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = device.name || 'Unknown';
    const details = document.createElement('small');
    details.textContent = device.address + ' · ' +
      (device.connected ? '已连接' : device.paired ? '已配对' : '未配对') +
      (saved === device.address ? ' · 开机自动重连设备' : '');
    info.append(name, details);
    const actions = document.createElement('div');
    actions.className = 'bt-device-actions';
    function button(label, action) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.disabled = busy || !online;
      b.addEventListener('click', () => execute('action',
        {action, address:device.address}));
      actions.append(b);
    }
    if (device.connected) button('断开', 'disconnect');
    else button(device.paired ? '连接' : '配对并连接', 'connect');
    if (device.paired || saved === device.address) button('忘记设备', 'forget');
    row.append(info, actions);
    return row;
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const data = await api('GET', 'status');
      connection.textContent = data.connected_address ? '已连接' :
        data.busy ? '操作中…' : data.saved_address ? '等待重连' : '未连接';
      const offline = !data.online;
      scan.disabled = pending || data.busy || offline;
      const phase = data.busy
        ? (data.action === 'scan' ? '正在后台扫描，请让手柄进入配对模式…' :
           '正在后台执行 ' + data.action + '，网页控制不受影响…')
        : data.connected_address ? 'Xbox 蓝牙已连接，按 Back + Start 获取运动控制权。'
        : data.autoconnect ? '已记住设备；蓝牙可用时后台会自动重连（不会自动使能电机）。'
        : '可扫描并选择 Xbox 手柄配对。';
      status.textContent = offline
        ? 'Uno Q 蓝牙后台服务未运行或尚未上线；检查 xgoduck-xbox systemd 服务。'
        : data.error ? '蓝牙操作失败：' + data.error
        : data.result ? data.result + '。' + phase : phase;
      const devices = Array.isArray(data.devices) ? data.devices : [];
      const signature = JSON.stringify([devices, data.saved_address,
                                         data.busy, data.online]);
      if (signature !== lastSignature) {
        lastSignature = signature;
        list.replaceChildren();
        for (const device of devices) {
          if (!/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(device.address)) continue;
          list.append(deviceRow(device, data.saved_address, data.busy, data.online));
        }
        if (!list.childElementCount) {
          const hint = document.createElement('p');
          hint.className = 'hint';
          hint.textContent = '尚未发现设备。请打开 Xbox 手柄并进入蓝牙配对模式，然后点击扫描。';
          list.append(hint);
        }
      }
    } catch (error) {
      scan.disabled = true;
      connection.textContent = '服务不可用';
      status.textContent = '无法读取蓝牙状态：' + error.message;
    } finally {
      loading = false;
    }
  }
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 1800);
})();
