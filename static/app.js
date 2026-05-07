(() => {
  const qp = new URLSearchParams(window.location.search);
  const token = qp.get('token') || '';
  const roleHint = qp.get('role') || 'student';
  if (!token) {
    document.body.innerHTML = '<h2>Zugriff verweigert (Token fehlt)</h2>';
    return;
  }

  const wsUrl = `ws://${location.hostname}:8081/?token=${encodeURIComponent(token)}&role=${encodeURIComponent(roleHint)}`;
  const MIN_LINE_WIDTH = 2;
  const MAX_LINE_WIDTH = 6;
  const PRESSURE_MULTIPLIER = 4;
  const MAX_PAGE_NUMBER = 500;
  const rooms = new Map();
  let me = { id: '', role: 'student', color: '#2563eb' };
  let activeRoom = null;
  let currentPage = 1;
  let drawing = null;
  let eraser = false;

  const board = document.getElementById('board');
  const tabs = document.getElementById('tabs');
  const frame = document.getElementById('pdfFrame');
  const canvas = document.getElementById('drawCanvas');
  const ctx = canvas.getContext('2d');
  const emptyState = document.getElementById('emptyState');
  const roleBadge = document.getElementById('roleBadge');
  const participants = document.getElementById('participants');
  const pageLabel = document.getElementById('pageLabel');
  const eraserBtn = document.getElementById('eraserBtn');
  const undoBtn = document.getElementById('undoBtn');
  const clearBtn = document.getElementById('clearBtn');
  const prevPageBtn = document.getElementById('prevPageBtn');
  const nextPageBtn = document.getElementById('nextPageBtn');
  const uploadArea = document.getElementById('uploadArea');
  const fileInput = document.getElementById('pdfFile');

  const ws = new WebSocket(wsUrl);

  const buildPdfUrl = (roomId) => `/api/pdf/${encodeURIComponent(roomId)}?token=${encodeURIComponent(token)}`;

  const drawStroke = (stroke) => {
    const points = stroke.points || [];
    if (points.length < 2) return;
    ctx.save();
    if (stroke.erase) {
      ctx.globalCompositeOperation = 'destination-out';
      ctx.strokeStyle = 'rgba(0,0,0,1)';
    } else {
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = stroke.color;
    }
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (let i = 1; i < points.length; i += 1) {
      const [x0, y0, p0] = points[i - 1];
      const [x1, y1, p1] = points[i];
      ctx.lineWidth = Math.min(
        MAX_LINE_WIDTH,
        Math.max(MIN_LINE_WIDTH, (stroke.width || MIN_LINE_WIDTH) + Math.max(p0, p1) * PRESSURE_MULTIPLIER),
      );
      ctx.beginPath();
      ctx.moveTo(x0 * canvas.width, y0 * canvas.height);
      ctx.lineTo(x1 * canvas.width, y1 * canvas.height);
      ctx.stroke();
    }
    ctx.restore();
  };

  const redraw = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!activeRoom) return;
    const room = rooms.get(activeRoom);
    (room?.strokes?.[String(currentPage)] || []).forEach(drawStroke);
  };

  const resize = () => {
    const rect = board.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(rect.width));
    canvas.height = Math.max(1, Math.floor(rect.height));
    redraw();
  };

  const renderTabs = () => {
    tabs.innerHTML = '';
    [...rooms.values()].forEach((room) => {
      const btn = document.createElement('button');
      btn.className = `tab${room.room === activeRoom ? ' active' : ''}`;
      btn.textContent = room.filename;
      btn.onclick = () => {
        activeRoom = room.room;
        currentPage = room.current_page || 1;
        renderRoom();
      };
      tabs.appendChild(btn);
    });
  };

  const renderRoom = () => {
    const room = rooms.get(activeRoom);
    renderTabs();
    if (!room) {
      emptyState.style.display = 'grid';
      frame.src = 'about:blank';
      pageLabel.textContent = 'Seite 1';
      redraw();
      return;
    }
    emptyState.style.display = 'none';
    frame.src = `${buildPdfUrl(room.room)}#page=${currentPage}`;
    pageLabel.textContent = `Seite ${currentPage}`;
    redraw();
  };

  const setParticipants = (payload) => {
    participants.textContent = `${payload.students || 0} Schüler`;
  };

  const uploadPdf = async (file) => {
    if (me.role !== 'teacher' || !file) return;
    const fd = new FormData();
    fd.set('file', file);
    try {
      const res = await fetch(`/api/upload?token=${encodeURIComponent(token)}`, {
        method: 'POST',
        headers: { 'X-User-Id': me.id },
        body: fd,
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Upload fehlgeschlagen (${res.status})`);
      }
    } catch (err) {
      window.alert(`PDF-Upload fehlgeschlagen: ${err.message || err}`);
    }
  };

  const toPoint = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const pressure = ev.pressure && ev.pressure > 0 ? ev.pressure : 0;
    return [
      Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (ev.clientY - rect.top) / rect.height)),
      Math.max(0, Math.min(1, pressure)),
    ];
  };

  canvas.addEventListener('pointerdown', (ev) => {
    if (!activeRoom) return;
    ev.preventDefault();
    canvas.setPointerCapture(ev.pointerId);
    drawing = [toPoint(ev)];
  });

  canvas.addEventListener('pointermove', (ev) => {
    if (!drawing) return;
    ev.preventDefault();
    drawing.push(toPoint(ev));
    drawStroke({ points: drawing.slice(-2), color: me.color, width: 2, erase: eraser });
  });

  canvas.addEventListener('pointerup', (ev) => {
    if (!drawing || !activeRoom) return;
    ev.preventDefault();
    drawing.push(toPoint(ev));
    ws.send(JSON.stringify({
      type: 'stroke', room: activeRoom, page: currentPage, points: drawing,
      color: me.color, width: 2, erase: eraser, user_id: me.id,
    }));
    drawing = null;
  });

  eraserBtn.onclick = () => {
    eraser = !eraser;
    eraserBtn.classList.toggle('active', eraser);
  };

  undoBtn.onclick = () => {
    if (!activeRoom) return;
    ws.send(JSON.stringify({ type: 'undo', room: activeRoom, page: currentPage, user_id: me.id }));
  };

  clearBtn.onclick = () => {
    if (!activeRoom || me.role !== 'teacher') return;
    ws.send(JSON.stringify({ type: 'clear', room: activeRoom, page: currentPage }));
  };

  prevPageBtn.onclick = () => {
    if (!activeRoom) return;
    currentPage = Math.max(1, currentPage - 1);
    ws.send(JSON.stringify({ type: 'page_change', room: activeRoom, page: currentPage }));
  };

  nextPageBtn.onclick = () => {
    if (!activeRoom) return;
    currentPage = Math.min(MAX_PAGE_NUMBER, currentPage + 1);
    ws.send(JSON.stringify({ type: 'page_change', room: activeRoom, page: currentPage }));
  };

  if (fileInput) fileInput.addEventListener('change', (ev) => uploadPdf(ev.target.files?.[0]));
  uploadArea.addEventListener('dragover', (ev) => { ev.preventDefault(); uploadArea.classList.add('dragover'); });
  uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
  uploadArea.addEventListener('drop', (ev) => {
    ev.preventDefault();
    uploadArea.classList.remove('dragover');
    uploadPdf(ev.dataTransfer?.files?.[0]);
  });

  ws.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (_err) {
      return;
    }

    if (msg.type === 'welcome') {
      me = { id: msg.user_id, role: msg.role, color: msg.color };
      roleBadge.textContent = msg.role === 'teacher' ? 'Lehrer' : 'Schüler';
      roleBadge.style.background = msg.color;
      roleBadge.style.color = '#fff';
      document.querySelectorAll('.teacher-only').forEach((el) => { el.style.display = msg.role === 'teacher' ? '' : 'none'; });
      (msg.rooms || []).forEach((room) => rooms.set(room.room, room));
      activeRoom = activeRoom || msg.rooms?.[0]?.room || null;
      currentPage = msg.rooms?.[0]?.current_page || 1;
      setParticipants(msg.participants || {});
      renderRoom();
      resize();
      return;
    }

    if (msg.type === 'participants') { setParticipants(msg); return; }

    if (msg.type === 'pdf_upload') {
      const existing = rooms.get(msg.room) || { strokes: {} };
      rooms.set(msg.room, {
        room: msg.room,
        filename: msg.filename,
        current_page: msg.page || 1,
        strokes: existing.strokes || {},
      });
      activeRoom = activeRoom || msg.room;
      currentPage = 1;
      renderRoom();
      return;
    }

    if (!msg.room || !rooms.has(msg.room)) return;
    const room = rooms.get(msg.room);

    if (msg.type === 'stroke') {
      room.strokes[String(msg.page)] = room.strokes[String(msg.page)] || [];
      room.strokes[String(msg.page)].push(msg);
      if (msg.room === activeRoom && Number(msg.page) === Number(currentPage)) drawStroke(msg);
      return;
    }

    if (msg.type === 'undo') {
      room.strokes[String(msg.page)] = (room.strokes[String(msg.page)] || []).filter((s) => s.id !== msg.stroke_id);
      if (msg.room === activeRoom && Number(msg.page) === Number(currentPage)) redraw();
      return;
    }

    if (msg.type === 'clear') {
      room.strokes[String(msg.page)] = [];
      if (msg.room === activeRoom && Number(msg.page) === Number(currentPage)) redraw();
      return;
    }

    if (msg.type === 'page_change') {
      room.current_page = msg.page;
      if (msg.room === activeRoom) {
        currentPage = msg.page;
        renderRoom();
      }
    }
  };

  window.addEventListener('resize', resize);
  window.addEventListener('orientationchange', resize);
})();
