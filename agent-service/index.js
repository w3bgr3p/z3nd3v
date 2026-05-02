import express from 'express'
import { createInterface } from 'readline'
import { spawn } from 'child_process'
import { existsSync, statSync } from 'fs'
import { dirname } from 'path'

const app  = express()
const PORT = parseInt(process.env.AGENT_PORT || '20129')
const sessions = new Map()
const activeProcesses = new Map()  // chatId -> child_process
const cfg = JSON.parse(process.env.AGENT_CONFIG || '{}')

const CLAUDE_CLI = cfg.claudeCli ||
  'C:/Users/' + (process.env.USERNAME || 'user') +
  '/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/cli.js'

console.log('[diag] execPath:', process.execPath)
console.log('[diag] execPath exists:', existsSync(process.execPath))
console.log('[diag] CLAUDE_CLI:', CLAUDE_CLI)
console.log('[diag] CLAUDE_CLI exists:', existsSync(CLAUDE_CLI))

const agentEnv = Object.fromEntries(
  Object.entries({ ...process.env }).filter(([, v]) => v !== undefined)
)
agentEnv.ANTHROPIC_BASE_URL                       = (cfg.omniRouteHost || 'http://localhost:20128/').replace(/\/$/, '') + '/v1'
agentEnv.ANTHROPIC_AUTH_TOKEN                     = cfg.authToken || ''
agentEnv.ANTHROPIC_API_KEY                        = ''
agentEnv.ANTHROPIC_MODEL                          = cfg.model || 'kr/claude-sonnet-4.5'
agentEnv.ANTHROPIC_SMALL_FAST_MODEL               = cfg.model || 'kr/claude-sonnet-4.5'
agentEnv.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = '1'
delete agentEnv.CLAUDECODE

function resolveDir(p) {
  if (!p) return process.cwd()
  try {
    return statSync(p).isDirectory() ? p : dirname(p)
  } catch {
    return process.cwd()
  }
}

app.use(express.json())

app.post('/chat', async (req, res) => {
  const { chatId, message, cwd, model } = req.body
  if (!chatId || !message) return res.status(400).json({ error: 'chatId and message required' })

  res.setHeader('Content-Type',  'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection',    'keep-alive')
  res.setHeader('Access-Control-Allow-Origin', '*')
  res.flushHeaders()

  const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)

  try {
    const sessionId = sessions.get(chatId)
    const spawnCwd  = resolveDir(cwd)

    // Create custom env with selected model
    const customEnv = { ...agentEnv }
    if (model) {
      customEnv.ANTHROPIC_MODEL = model
      customEnv.ANTHROPIC_SMALL_FAST_MODEL = model
    }

    const args = [
      CLAUDE_CLI,
      '--output-format', 'stream-json',
      '--print',
      '--dangerously-skip-permissions',
      '--verbose',
    ]
    if (sessionId) args.push('--resume', sessionId)

    console.log('[spawn] cwd:', spawnCwd)
    console.log('[spawn] model:', model || customEnv.ANTHROPIC_MODEL)

    const proc = spawn(process.execPath, args, {
      cwd:         spawnCwd,
      env:         customEnv,
      stdio:       ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })

    // Store active process for interrupt capability
    activeProcesses.set(chatId, proc)

    proc.on('error', err => {
      console.error('[spawn-error]', err.code, err.message)
      send('error', { error: err.message })
      activeProcesses.delete(chatId)
    })

    proc.stdin.write(message)
    proc.stdin.end()

    const rl = createInterface({ input: proc.stdout })

    rl.on('line', line => {
      if (!line.trim()) return
      let evt
      try { evt = JSON.parse(line) } catch { return }

      if (evt.type === 'system' && evt.subtype === 'init') {
        sessions.set(chatId, evt.session_id)
      }
      if (evt.type === 'assistant') {
        for (const block of evt.message?.content || []) {
          if (block.type === 'text' && block.text) send('delta', { text: block.text })
          if (block.type === 'tool_use') send('tool', { name: block.name, input: block.input })
        }
      }
      if (evt.type === 'result' && evt.subtype !== 'success') {
        // only send result for errors — success content already streamed via assistant events
        const text = `Error: ${evt.errors?.join('; ') || 'Agent error'}`
        send('delta', { text })
      }
    })

    proc.stderr.on('data', d => console.error('[claude]', d.toString().trim()))

    await new Promise(resolve => {
      proc.on('close', resolve)
      proc.on('error', resolve)
    })

    // Clean up active process tracking
    activeProcesses.delete(chatId)

    send('done', { sessionId: sessions.get(chatId) || '' })

  } catch (err) {
    console.error('[chat-error]', err.message)
    send('error', { error: err.message || String(err) })
    activeProcesses.delete(chatId)
  } finally {
    res.end()
  }
})

app.delete('/session/:chatId', (req, res) => res.json({ ok: sessions.delete(req.params.chatId) }))
app.get('/sessions', (req, res) => res.json({ sessions: Object.fromEntries(sessions) }))
app.get('/health',   (req, res) => res.json({ ok: true }))

app.post('/interrupt', (req, res) => {
  const { chatId } = req.body
  if (!chatId) return res.status(400).json({ error: 'chatId required' })

  const proc = activeProcesses.get(chatId)
  if (!proc) return res.json({ ok: false, error: 'No active process for this chatId' })

  try {
    proc.kill('SIGTERM')
    activeProcesses.delete(chatId)
    console.log(`[interrupt] killed process for chatId: ${chatId}`)
    res.json({ ok: true })
  } catch (err) {
    console.error('[interrupt-error]', err.message)
    res.status(500).json({ ok: false, error: err.message })
  }
})

app.listen(PORT, '127.0.0.1', () => console.log(`[agent-service] listening on 127.0.0.1:${PORT}`))
