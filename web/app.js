// activeToken 只代表当前这一轮画面和声音。过期回调必须直接丢掉。
const stage = document.querySelector("#stage")
const statusLabel = document.querySelector("#status-label")
const modeLabel = document.querySelector("#mode")
const echo = document.querySelector("#echo")
const subtitle = document.querySelector("#subtitle")
const line = document.querySelector("#line")
const sendButton = document.querySelector("#send")
const talkButton = document.querySelector("#talk")
const clip = document.querySelector("#clip")
const toast = document.querySelector("#toast")
const gate = document.querySelector("#gate")
const gateError = document.querySelector("#gate-error")
const failMedia = document.querySelector("#fail-media")

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
const recognition = SpeechRecognition ? new SpeechRecognition() : null

let sessionId = ""
let activeToken = 0
let serverTurn = 0
let holding = false
let listenEpoch = 0
let hold = null
let inflight = null
let player = null
let zhVoice = null
const pending = new Set()
const labels = { idle: "待机", listening: "倾听", thinking: "思考", speaking: "说话" }

if (recognition) {
  recognition.lang = "zh-CN"
  recognition.continuous = false
  recognition.interimResults = true
  recognition.onresult = (event) => {
    if (!hold || hold.epoch !== listenEpoch) return
    let finalText = ""
    let interim = ""
    for (let i = 0; i < event.results.length; i += 1) {
      const piece = event.results[i][0].transcript
      if (event.results[i].isFinal) finalText += piece
      else interim += piece
    }
    hold.text = (finalText || interim).trim()
    if (hold.token === activeToken) subtitle.textContent = hold.text
  }
  recognition.onerror = (event) => {
    if (!hold || hold.epoch !== listenEpoch) return
    if (event.error === "not-allowed" || event.error === "service-not-allowed") {
      hold.submitted = true
      notify("需要允许麦克风，或改用文字")
      setStatus("idle")
    } else if (event.error === "network") {
      hold.networkError = true
    }
  }
  recognition.onend = () => {
    if (!hold || hold.epoch !== listenEpoch) return
    if (holding) {
      try { recognition.start() } catch (err) { /* 浏览器会在一次识别结束后自己停 */ }
      return
    }
    finishHold()
  }
}

function pickVoice() {
  const voices = window.speechSynthesis?.getVoices?.() || []
  zhVoice = voices.find((item) => /zh|cmn/i.test(item.lang)) || null
}
pickVoice()
window.speechSynthesis?.addEventListener("voiceschanged", pickVoice)

function notify(message) {
  toast.textContent = message
  toast.dataset.show = "1"
  window.setTimeout(() => {
    if (toast.textContent === message) toast.dataset.show = "0"
  }, 2600)
}

function setStatus(status) {
  stage.dataset.status = status
  statusLabel.textContent = labels[status] || status
}

function later(fn, ms) {
  const id = window.setTimeout(() => {
    pending.delete(id)
    fn()
  }, ms)
  pending.add(id)
}

function clearPending() {
  for (const id of pending) window.clearTimeout(id)
  pending.clear()
}

function stopAudio() {
  if (!player) return
  player.pause()
  player.src = ""
  player = null
}

function cancelPlayback() {
  clearPending()
  stopAudio()
  try { window.speechSynthesis?.cancel() } catch (err) { /* 有些浏览器在空队列上会抛错 */ }
  subtitle.textContent = ""
  echo.textContent = ""
  clip.hidden = true
  clip.dataset.state = ""
  stage.dataset.fx = ""
}

function abortInflight() {
  if (!inflight) return
  inflight.abort()
  inflight = null
}

function invalidateLocal() {
  activeToken += 1
  cancelPlayback()
  abortInflight()
  if (sessionId && serverTurn > 0) {
    const turnId = serverTurn
    fetch(`/api/sessions/${sessionId}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ turnId }),
    }).catch(() => {})
  }
  return activeToken
}

function unlockControls() {
  document.querySelector("#dock").hidden = false
  line.disabled = false
  sendButton.disabled = false
  talkButton.disabled = false
}

function primeSpeech() {
  if (!window.speechSynthesis) return
  const utterance = new SpeechSynthesisUtterance("\u200b")
  utterance.volume = 0
  utterance.rate = 10
  window.speechSynthesis.speak(utterance)
}

function finishSpeaking(myToken) {
  if (myToken !== activeToken) return
  if (stage.dataset.status !== "speaking") return
  setStatus("idle")
}

function startSubtitle(text, myToken) {
  let index = 0
  subtitle.textContent = ""
  const tick = () => {
    if (myToken !== activeToken) return
    index = Math.min(text.length, index + 1)
    subtitle.textContent = text.slice(0, index)
    if (index < text.length) later(tick, 38)
  }
  tick()
}

function speakWithBrowser(text, myToken) {
  if (!window.speechSynthesis) {
    notify("语音不可用，先看字幕")
    later(() => finishSpeaking(myToken), Math.min(9000, 1200 + text.length * 90))
    return
  }
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = "zh-CN"
  utterance.rate = 1.02
  if (zhVoice) utterance.voice = zhVoice
  utterance.onend = () => finishSpeaking(myToken)
  utterance.onerror = () => finishSpeaking(myToken)
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(utterance)
  later(() => finishSpeaking(myToken), Math.min(20000, 5000 + text.length * 180))
}

function playSpeech(speech, text, myToken) {
  if (!speech?.base64) {
    speakWithBrowser(text, myToken)
    return
  }
  player = new Audio(`data:${speech.mime || "audio/mpeg"};base64,${speech.base64}`)
  player.onended = () => finishSpeaking(myToken)
  player.onerror = () => speakWithBrowser(text, myToken)
  player.play().catch(() => speakWithBrowser(text, myToken))
  later(() => finishSpeaking(myToken), Math.min(16000, 2200 + text.length * 140))
}

function startMedia(media, myToken) {
  if (!media || media.type !== "sequence" || media.id !== "door_light") return
  const shouldFail = failMedia.checked
  clip.hidden = false
  clip.dataset.state = "loading"
  later(() => {
    if (myToken !== activeToken) return
    if (shouldFail) {
      failMedia.checked = false
      clip.dataset.state = "failed"
      notify("这段画面没加载出来")
      later(() => {
        if (myToken !== activeToken) return
        if (clip.dataset.state === "failed") clip.hidden = true
      }, 1700)
      return
    }
    clip.dataset.state = "playing"
    later(() => {
      if (myToken !== activeToken) return
      clip.hidden = true
      clip.dataset.state = ""
    }, 2600)
  }, 700)
}

function applyDirective(directive, myToken, speech) {
  if (myToken !== activeToken || !directive) return
  stage.dataset.emotion = directive.emotion
  stage.dataset.action = directive.action
  stage.dataset.scene = directive.scene
  if (directive.fx === "lightning") {
    stage.dataset.fx = "lightning"
    later(() => {
      if (stage.dataset.fx === "lightning") stage.dataset.fx = ""
    }, 520)
  }
  setStatus("speaking")
  startSubtitle(directive.say, myToken)
  playSpeech(speech, directive.say, myToken)
  if (directive.media) startMedia(directive.media, myToken)
}

async function commitText(text, myToken, turnId, shown) {
  echo.textContent = shown ?? (text.startsWith("__") ? "" : text)
  subtitle.textContent = ""
  const controller = new AbortController()
  inflight = controller
  try {
    const response = await fetch(`/api/sessions/${sessionId}/turns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ turnId, text }),
      signal: controller.signal,
    })
    const data = await response.json().catch(() => ({}))
    if (myToken !== activeToken) return
    if (!response.ok) {
      notify(data.detail || "她没接上话")
      setStatus("idle")
      return
    }
    if (data.cancelled) return
    if (data.error) {
      notify(data.error.message || "她没接上话")
      setStatus("idle")
      return
    }
    if (!data.directive) {
      notify("她没接上话")
      setStatus("idle")
      return
    }
    applyDirective(data.directive, myToken, data.speech)
  } catch (error) {
    if (error.name === "AbortError") return
    if (myToken !== activeToken) return
    notify("连接断了，再发一次就好")
    setStatus("idle")
  } finally {
    if (inflight === controller) inflight = null
  }
}

function beginTurn(text, shown) {
  const token = invalidateLocal()
  serverTurn += 1
  setStatus("thinking")
  commitText(text, token, serverTurn, shown)
}

async function uploadAudio(blob, myToken) {
  const form = new FormData()
  form.append("file", blob, "speech.webm")
  form.append("turnId", String(serverTurn))
  const controller = new AbortController()
  inflight = controller
  try {
    const response = await fetch("/api/transcribe", { method: "POST", body: form, signal: controller.signal })
    const data = await response.json().catch(() => ({}))
    if (myToken !== activeToken) return ""
    return (data.text || "").trim()
  } catch (error) {
    if (error.name === "AbortError") return ""
    return ""
  } finally {
    if (inflight === controller) inflight = null
  }
}

function stopRecorder(current) {
  const recorder = current.recorder
  if (!recorder || recorder.state !== "recording") {
    current.recStopped = true
    return
  }
  recorder.onstop = () => {
    current.stream?.getTracks().forEach((track) => track.stop())
    current.blob = new Blob(current.chunks, { type: recorder.mimeType || "audio/webm" })
    current.recStopped = true
    if (!holding && current.epoch === listenEpoch) finishHold()
  }
  recorder.stop()
}

async function startRecorder(current) {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    notify("这个浏览器不能直接听写，请改用文字")
    return
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    if (current.epoch !== listenEpoch || !holding) {
      stream.getTracks().forEach((track) => track.stop())
      current.recStopped = true
      if (!holding) finishHold()
      return
    }
    const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : ""
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
    current.stream = stream
    current.recorder = recorder
    current.chunks = []
    recorder.ondataavailable = (event) => {
      if (event.data.size) current.chunks.push(event.data)
    }
    recorder.start()
  } catch (err) {
    notify("麦克风没能打开")
  }
}

function finishHold() {
  if (!hold || hold.submitted || hold.epoch !== listenEpoch || hold.token !== activeToken) return
  if (hold.recorder && !hold.recStopped) return
  const current = hold
  const text = current.text.trim()
  if (text) {
    current.submitted = true
    serverTurn += 1
    setStatus("thinking")
    commitText(text, current.token, serverTurn)
    return
  }
  if (current.blob && !current.uploaded) {
    current.uploaded = true
    setStatus("thinking")
    uploadAudio(current.blob, current.token).then((transcript) => {
      if (current.token !== activeToken || current.submitted) return
      current.submitted = true
      if (!transcript) {
        notify(current.networkError ? "语音识别连不上，先用文字" : "没听清，再说一次")
        setStatus("idle")
        return
      }
      serverTurn += 1
      commitText(transcript, current.token, serverTurn)
    })
    return
  }
  current.submitted = true
  notify(current.networkError ? "语音识别连不上，先用文字" : "没听清，再说一次")
  setStatus("idle")
}

function startHold() {
  const token = invalidateLocal()
  holding = true
  listenEpoch += 1
  hold = { token, epoch: listenEpoch, text: "", submitted: false, chunks: [], recStopped: true }
  setStatus("listening")
  talkButton.classList.add("down")
  talkButton.setAttribute("aria-pressed", "true")
  if (recognition) {
    try { recognition.start() } catch (err) { notify("麦克风没能打开") }
    return
  }
  hold.recStopped = false
  startRecorder(hold)
}

function endHold() {
  if (!holding) return
  holding = false
  talkButton.classList.remove("down")
  talkButton.setAttribute("aria-pressed", "false")
  if (recognition) {
    try { recognition.stop() } catch (err) { finishHold() }
    later(() => finishHold(), 1500)
    return
  }
  if (!hold) return
  stopRecorder(hold)
  if (hold.recStopped) finishHold()
}

document.querySelector("#composer").addEventListener("submit", (event) => {
  event.preventDefault()
  const text = line.value.trim()
  if (!text || !sessionId) return
  line.value = ""
  beginTurn(text)
})

talkButton.addEventListener("pointerdown", (event) => {
  event.preventDefault()
  if (!sessionId || holding) return
  talkButton.setPointerCapture(event.pointerId)
  startHold()
})
talkButton.addEventListener("pointerup", endHold)
talkButton.addEventListener("pointercancel", endHold)

document.querySelector("#force-timeout").addEventListener("click", () => {
  if (!sessionId) return
  beginTurn("__timeout__", "")
})

document.querySelector("#enter").addEventListener("click", async () => {
  const button = document.querySelector("#enter")
  button.disabled = true
  gateError.textContent = ""
  primeSpeech()
  try {
    const response = await fetch("/api/sessions", { method: "POST" })
    if (!response.ok) throw new Error("fail")
    const data = await response.json()
    sessionId = data.sessionId
    modeLabel.textContent = data.mode === "mock" ? "剧本演示" : "模型"
    gate.hidden = true
    unlockControls()
    activeToken = 1
    applyDirective(data.opening, 1, null)
  } catch (err) {
    button.disabled = false
    gateError.textContent = "没能推进去，确认服务已启动后再试"
  }
})
