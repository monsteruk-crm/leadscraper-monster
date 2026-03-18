import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  AppBar,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Drawer,
  FormControlLabel,
  LinearProgress,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  MenuItem,
  Pagination,
  Paper,
  Stack,
  Switch,
  Tab,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Toolbar,
  Typography,
} from '@mui/material'
import { ReactTerminal } from 'react-terminal'
import type { SxProps, Theme } from '@mui/material/styles'

type HealthResponse = {
  status: string
  db?: string
  detail?: string
  leads?: number
  visited_urls?: number
  runs?: number
  sessions?: number
}

type StatsResponse = {
  leads: number
  visited_urls: number
  runs: number
  sessions: number
}

type LeadRecord = {
  id: number
  company_name?: string
  first_name?: string
  last_name?: string
  contact_name?: string
  title?: string
  role?: string
  email?: string
  phone?: string
  website?: string
  country?: string
  city?: string
  category?: string
  confidence?: number
  status?: string
  notes?: string
  owner?: string
  last_touch?: string
  source_url?: string
  archived?: boolean
  opt_out?: boolean
}

type LeadsResponse = {
  leads: LeadRecord[]
  total: number
  page: number
  page_size: number
}

type SessionRecord = {
  id: number
  name: string
  updated_at?: string
  turn_count?: number
}

type SessionTurn = {
  id?: number
  role: string
  content: string
  mode?: string
  created_at?: string
}

type RunRecord = {
  id: number
  session_id?: number
  keywords?: string[]
  pages_crawled?: number
  leads_new?: number
  leads_duplicate?: number
  leads_discarded?: number
  finished_at?: string
  created_at?: string
}

type ConfigState = {
  keywords: string[]
  max_pages: number
  target_new_leads: number
  request_delay_seconds: number
  ai_enrichment_enabled: boolean
  ai_confidence_threshold: number
}

type PipelineStage = {
  name: string
  count: string
  hint: string
  tone: DashboardMetric['tone']
}

const navItems = ['Overview', 'Leads', 'Terminal', 'Settings'] as const
type NavItem = (typeof navItems)[number]

type DashboardMetric = {
  label: string
  value: string
  note: string
  tone: 'primary' | 'success' | 'warning' | 'info'
}

type MetricTileProps = {
  label: string
  value: string
  note: string
  tone: DashboardMetric['tone']
  size?: 'default' | 'compact'
}

const defaultConfig: ConfigState = {
  keywords: [],
  max_pages: 3,
  target_new_leads: 0,
  request_delay_seconds: 1.5,
  ai_enrichment_enabled: true,
  ai_confidence_threshold: 0,
}

const leadStatusOptions = ['New', 'Queued', 'Reviewing', 'Qualified', 'Contacted'] as const

const emptyLeads: LeadsResponse = {
  leads: [],
  total: 0,
  page: 1,
  page_size: 50,
}

const emptyStats: StatsResponse = {
  leads: 0,
  visited_urls: 0,
  runs: 0,
  sessions: 0,
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    let detail = `Request failed with ${response.status}`
    try {
      const data = (await response.json()) as { detail?: string }
      detail = data.detail ?? detail
    } catch {
      const text = await response.text()
      if (text) {
        detail = text
      }
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

async function collectSseEvents(url: string, body: unknown): Promise<Record<string, unknown>[]> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  if (!response.ok || !response.body) {
    const text = await response.text()
    throw new Error(text || `Request failed with ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const events: Record<string, unknown>[] = []
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data:')) {
        continue
      }
      const payload = line.slice(5).trim()
      if (!payload) {
        continue
      }
      events.push(JSON.parse(payload) as Record<string, unknown>)
    }
  }

  if (buffer.startsWith('data:')) {
    const payload = buffer.slice(5).trim()
    if (payload) {
      events.push(JSON.parse(payload) as Record<string, unknown>)
    }
  }

  return events
}

function formatNumber(value: number | undefined): string {
  return new Intl.NumberFormat('en-GB').format(value ?? 0)
}

function formatRelativeTime(value?: string): string {
  if (!value) {
    return 'unknown'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  const seconds = Math.round((date.getTime() - Date.now()) / 1000)
  const thresholds = [
    { unit: 'year', size: 60 * 60 * 24 * 365 },
    { unit: 'month', size: 60 * 60 * 24 * 30 },
    { unit: 'day', size: 60 * 60 * 24 },
    { unit: 'hour', size: 60 * 60 },
    { unit: 'minute', size: 60 },
  ] as const

  const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  for (const threshold of thresholds) {
    if (Math.abs(seconds) >= threshold.size) {
      return formatter.format(Math.round(seconds / threshold.size), threshold.unit)
    }
  }

  return formatter.format(seconds, 'second')
}

function buildContactName(lead: LeadRecord): string {
  const fromParts = [lead.first_name, lead.last_name].filter(Boolean).join(' ').trim()
  return fromParts || lead.contact_name || '-'
}

function leadConfidence(lead: LeadRecord): number {
  return typeof lead.confidence === 'number' ? lead.confidence : 0
}

function MetricTile({ label, value, note, tone, size = 'default' }: MetricTileProps) {
  const metricToneStyles: Record<DashboardMetric['tone'], SxProps<Theme>> = {
    primary: {
      bgcolor: 'rgba(124, 58, 237, 0.16)',
      borderColor: 'rgba(196, 181, 253, 0.25)',
      color: '#ddd6fe',
    },
    success: {
      bgcolor: 'rgba(34, 197, 94, 0.16)',
      borderColor: 'rgba(134, 239, 172, 0.25)',
      color: '#bbf7d0',
    },
    warning: {
      bgcolor: 'rgba(245, 158, 11, 0.16)',
      borderColor: 'rgba(253, 224, 71, 0.25)',
      color: '#fde68a',
    },
    info: {
      bgcolor: 'rgba(56, 189, 248, 0.16)',
      borderColor: 'rgba(125, 211, 252, 0.25)',
      color: '#bae6fd',
    },
  }

  return (
    <Card
      variant="outlined"
      sx={{
        bgcolor: 'rgba(255,255,255,0.02)',
        height: '100%',
        ...(size === 'compact' ? { minHeight: 0 } : null),
      }}
    >
      <CardContent sx={{ p: size === 'compact' ? 1.5 : 2 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
          <Typography
            variant={size === 'compact' ? 'caption' : 'body2'}
            color="text.secondary"
            sx={{
              textTransform: 'uppercase',
              fontSize: size === 'compact' ? '0.72rem' : '0.8rem',
              fontWeight: 800,
              whiteSpace: 'nowrap',
            }}
          >
            {label}
          </Typography>
          <Box
            sx={{
              width: size === 'compact' ? 40 : 48,
              height: size === 'compact' ? 40 : 48,
              borderRadius: '50%',
              display: 'grid',
              placeItems: 'center',
              ...metricToneStyles[tone],
              border: '1px solid',
              flexShrink: 0,
            }}
          >
            <Typography variant={size === 'compact' ? 'body1' : 'h6'} sx={{ fontWeight: 700, lineHeight: 1 }}>
              {value}
            </Typography>
          </Box>
        </Stack>
        <Typography variant={size === 'compact' ? 'caption' : 'body2'} color="text.secondary">
          {note}
        </Typography>
      </CardContent>
    </Card>
  )
}

function App() {
  const contentSectionRef = useRef<HTMLDivElement | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [stats, setStats] = useState<StatsResponse>(emptyStats)
  const [configDraft, setConfigDraft] = useState<ConfigState>(defaultConfig)
  const [sessions, setSessions] = useState<SessionRecord[]>([])
  const [sessionHistory, setSessionHistory] = useState<SessionTurn[]>([])
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [leadResponse, setLeadResponse] = useState<LeadsResponse>(emptyLeads)
  const [tab, setTab] = useState<NavItem>('Overview')
  const [search, setSearch] = useState('')
  const [leadPage, setLeadPage] = useState(1)
  const [includeArchived, setIncludeArchived] = useState(false)
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [leadDetail, setLeadDetail] = useState<LeadRecord | null>(null)
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null)
  const [activeSessionName, setActiveSessionName] = useState('')
  const [newSessionName, setNewSessionName] = useState('')
  const [renameSessionValue, setRenameSessionValue] = useState('')
  const [dashboardLoading, setDashboardLoading] = useState(true)
  const [leadsLoading, setLeadsLoading] = useState(false)
  const [sessionLoading, setSessionLoading] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [terminalBusy, setTerminalBusy] = useState(false)
  const [terminalStatus, setTerminalStatus] = useState<string>('Ready')
  const [terminalError, setTerminalError] = useState<string | null>(null)

  const blurActiveElement = useCallback(() => {
    const active = document.activeElement
    if (active instanceof HTMLElement) {
      active.blur()
    }
  }, [])

  const closeLeadDrawer = useCallback(() => {
    blurActiveElement()
    setLeadDetail(null)
  }, [blurActiveElement])

  const closeSessionsDrawer = useCallback(() => {
    blurActiveElement()
    setSessionsOpen(false)
  }, [blurActiveElement])

  const closeSettingsDialog = useCallback(() => {
    blurActiveElement()
    setSettingsOpen(false)
  }, [blurActiveElement])

  const openSessionsDrawer = useCallback(() => {
    blurActiveElement()
    setSessionsOpen(true)
  }, [blurActiveElement])

  const openSettingsDialog = useCallback(() => {
    blurActiveElement()
    setSettingsOpen(true)
  }, [blurActiveElement])

  const openLeadDetails = useCallback(
    (lead: LeadRecord) => {
      blurActiveElement()
      setLeadDetail(lead)
    },
    [blurActiveElement],
  )

  const loadHealth = useCallback(async (signal?: AbortSignal) => {
    const payload = await fetchJson<HealthResponse>('/api/health', { signal })
    setHealth(payload)
    return payload
  }, [])

  const loadStats = useCallback(async (signal?: AbortSignal) => {
    const payload = await fetchJson<StatsResponse>('/api/stats', { signal })
    setStats(payload)
    return payload
  }, [])

  const loadConfig = useCallback(async (signal?: AbortSignal) => {
    const payload = await fetchJson<ConfigState>('/api/config', { signal })
    setConfigDraft(payload)
    return payload
  }, [])

  const loadRuns = useCallback(async (signal?: AbortSignal) => {
    const payload = await fetchJson<RunRecord[]>('/api/runs?limit=8', { signal })
    setRuns(payload)
    return payload
  }, [])

  const loadSessions = useCallback(
    async (signal?: AbortSignal) => {
      const payload = await fetchJson<SessionRecord[]>('/api/sessions', { signal })
      setSessions(payload)
      setActiveSessionId((currentId) => {
        const current = payload.find((session) => session.id === currentId)
        const next = current ?? payload[0] ?? null
        if (next) {
          setActiveSessionName(next.name)
          setRenameSessionValue(next.name)
          return next.id
        }
        setActiveSessionName('')
        setRenameSessionValue('')
        return null
      })
      return payload
    },
    [],
  )

  const loadSessionHistory = useCallback(async (sessionId: number, limit = 20) => {
    setSessionLoading(true)
    try {
      const payload = await fetchJson<SessionTurn[]>(`/api/sessions/${sessionId}/history?limit=${limit}`)
      setSessionHistory(payload)
      return payload
    } finally {
      setSessionLoading(false)
    }
  }, [])

  const loadLeads = useCallback(
    async (query: string, archived: boolean, page: number, signal?: AbortSignal) => {
      setLeadsLoading(true)
      try {
        const params = new URLSearchParams({
          page: String(page),
          page_size: '50',
          search: query,
          include_archived: archived ? 'true' : 'false',
        })
        const payload = await fetchJson<LeadsResponse>(`/api/leads?${params.toString()}`, { signal })
        setLeadResponse(payload)
        return payload
      } finally {
        if (!signal?.aborted) {
          setLeadsLoading(false)
        }
      }
    },
    [],
  )

  const refreshAll = useCallback(async (query = search, archived = includeArchived, page = leadPage) => {
    setError(null)
    setNotice(null)
    setDashboardLoading(true)
    try {
      await Promise.all([
        loadHealth(),
        loadStats(),
        loadConfig(),
        loadSessions(),
        loadRuns(),
        loadLeads(query, archived, page),
      ])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard')
    } finally {
      setDashboardLoading(false)
    }
  }, [includeArchived, leadPage, loadConfig, loadHealth, loadLeads, loadRuns, loadSessions, loadStats, search])

  useEffect(() => {
    const controller = new AbortController()
    setError(null)
    setNotice(null)
    setDashboardLoading(true)

    void Promise.all([
      loadHealth(controller.signal),
      loadStats(controller.signal),
      loadConfig(controller.signal),
      loadSessions(controller.signal),
      loadRuns(controller.signal),
      loadLeads('', false, 1, controller.signal),
    ])
      .catch((err) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : 'Failed to load dashboard')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setDashboardLoading(false)
        }
      })

    return () => controller.abort()
  }, [loadConfig, loadHealth, loadLeads, loadRuns, loadSessions, loadStats])

  useEffect(() => {
    const controller = new AbortController()
    void loadLeads(search, includeArchived, leadPage, controller.signal).catch((err) => {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : 'Failed to load leads')
      }
    })
    return () => controller.abort()
  }, [includeArchived, leadPage, loadLeads, search])

  useEffect(() => {
    if (!activeSessionId) {
      setSessionHistory([])
      return
    }

    void loadSessionHistory(activeSessionId).catch((err) => {
      setError(err instanceof Error ? err.message : 'Failed to load session history')
    })
  }, [activeSessionId, loadSessionHistory])

  const reloadOperationalData = useCallback(async () => {
    await Promise.all([loadHealth(), loadStats(), loadRuns(), loadSessions(), loadLeads(search, includeArchived, leadPage)])
  }, [includeArchived, leadPage, loadHealth, loadLeads, loadRuns, loadSessions, loadStats, search])

  const setActiveSession = useCallback((session: SessionRecord) => {
    setActiveSessionId(session.id)
    setActiveSessionName(session.name)
    setRenameSessionValue(session.name)
  }, [])

  const handleCreateSession = useCallback(async () => {
    setWorking(true)
    setError(null)
    setNotice(null)
    try {
      const payload = await fetchJson<SessionRecord>('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newSessionName.trim() ? { name: newSessionName.trim() } : {}),
      })
      setNewSessionName('')
      await loadSessions()
      setActiveSession(payload)
      setNotice(`Created session #${payload.id}`)
      setSessionsOpen(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create session')
    } finally {
      setWorking(false)
    }
  }, [loadSessions, newSessionName, setActiveSession])

  const handleRenameSession = useCallback(async () => {
    if (!activeSessionId || !renameSessionValue.trim()) {
      return
    }

    setWorking(true)
    setError(null)
    setNotice(null)
    try {
      await fetchJson<{ status: string }>(`/api/sessions/${activeSessionId}/rename`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: renameSessionValue.trim() }),
      })
      setActiveSessionName(renameSessionValue.trim())
      await loadSessions()
      setNotice(`Renamed session #${activeSessionId}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rename session')
    } finally {
      setWorking(false)
    }
  }, [activeSessionId, loadSessions, renameSessionValue])

  const handleSaveSettings = useCallback(async () => {
    setSavingSettings(true)
    setError(null)
    setNotice(null)
    try {
      await fetchJson<{ status: string }>('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configDraft),
      })
      await Promise.all([loadConfig(), loadHealth()])
      setNotice('Settings saved')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      setSavingSettings(false)
    }
  }, [configDraft, loadConfig, loadHealth])

  const handleArchiveToggle = useCallback(
    async (lead: LeadRecord) => {
      setWorking(true)
      setError(null)
      setNotice(null)
      try {
        await fetchJson<{ status: string }>(`/api/leads/${lead.id}/archive?archived=${(!lead.archived).toString()}`, {
          method: 'PATCH',
        })
        await Promise.all([loadLeads(search, includeArchived, leadPage), loadStats(), loadHealth()])
        if (leadDetail?.id === lead.id) {
          setLeadDetail({ ...lead, archived: !lead.archived })
        }
        setNotice(`${lead.archived ? 'Restored' : 'Archived'} lead #${lead.id}`)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to update lead')
      } finally {
        setWorking(false)
      }
    },
    [includeArchived, leadDetail?.id, leadPage, loadHealth, loadLeads, loadStats, search],
  )

  const applyLeadUpdateLocally = useCallback((leadId: number, patch: Partial<LeadRecord>) => {
    setLeadResponse((current) => ({
      ...current,
      leads: current.leads.map((lead) => (lead.id === leadId ? { ...lead, ...patch } : lead)),
    }))
    setLeadDetail((current) => (current?.id === leadId ? { ...current, ...patch } : current))
  }, [])

  const handleLeadPatch = useCallback(
    async (leadId: number, patch: Partial<LeadRecord>, successMessage: string) => {
      setWorking(true)
      setError(null)
      setNotice(null)
      try {
        await fetchJson<{ status: string; updated: string[] }>(`/api/leads/${leadId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        })
        applyLeadUpdateLocally(leadId, patch)
        setNotice(successMessage)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to save lead')
      } finally {
        setWorking(false)
      }
    },
    [applyLeadUpdateLocally],
  )

  const handleDbInit = useCallback(async () => {
    setWorking(true)
    setError(null)
    setNotice(null)
    try {
      const payload = await fetchJson<{ message?: string }>('/api/db/init', { method: 'POST' })
      await refreshAll(search, includeArchived, leadPage)
      setNotice(payload.message ?? 'Database initialised')
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to initialise database')
    } finally {
      setWorking(false)
    }
    return false
  }, [includeArchived, leadPage, refreshAll, search])

  const handleDbReset = useCallback(async () => {
    if (!window.confirm('Reset the database and wipe all data?')) {
      return false
    }
    setWorking(true)
    setError(null)
    setNotice(null)
    try {
      const payload = await fetchJson<{ message?: string }>('/api/db/reset', { method: 'POST' })
      setSearch('')
      setIncludeArchived(false)
      setLeadPage(1)
      setLeadDetail(null)
      await refreshAll('', false, 1)
      setNotice(payload.message ?? 'Database reset')
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset database')
    } finally {
      setWorking(false)
    }
    return false
  }, [refreshAll])

  const handleExportLeads = useCallback(() => {
    window.open('/api/leads/export', '_blank', 'noopener,noreferrer')
  }, [])

  const qualifiedLeadCount = useMemo(() => {
    const threshold = Math.max(0.7, configDraft.ai_confidence_threshold)
    return leadResponse.leads.filter((lead) => leadConfidence(lead) >= threshold).length
  }, [configDraft.ai_confidence_threshold, leadResponse.leads])

  const lowConfidenceCount = useMemo(() => {
    const threshold = Math.max(0.4, configDraft.ai_confidence_threshold || 0.4)
    return leadResponse.leads.filter((lead) => leadConfidence(lead) < threshold).length
  }, [configDraft.ai_confidence_threshold, leadResponse.leads])

  const latestRun = runs[0]
  const totalPagesInRecentRuns = useMemo(
    () => runs.reduce((sum, run) => sum + (run.pages_crawled ?? 0), 0),
    [runs],
  )

  const dashboardMetrics = useMemo<DashboardMetric[]>(
    () => [
      {
        label: 'Qualified leads',
        value: formatNumber(qualifiedLeadCount),
        note: 'Loaded rows above confidence threshold',
        tone: 'success',
      },
      {
        label: 'Low confidence',
        value: formatNumber(lowConfidenceCount),
        note: 'Needs operator review in current results',
        tone: 'warning',
      },
      {
        label: 'Open sessions',
        value: formatNumber(sessions.length),
        note: activeSessionId ? `Active session #${activeSessionId}` : 'No active session yet',
        tone: 'info',
      },
      {
        label: 'API health',
        value: health?.status === 'ok' ? 'OK' : 'ERR',
        note: health?.db ? `Python API ${health.db}` : health?.detail ?? 'Waiting for API',
        tone: health?.status === 'ok' ? 'primary' : 'warning',
      },
    ],
    [activeSessionId, health?.db, health?.detail, health?.status, lowConfidenceCount, qualifiedLeadCount, sessions.length],
  )

  const summaryCards = useMemo(
    () => [
      {
        label: 'Leads',
        value: formatNumber(stats.leads),
        hint: `${formatNumber(qualifiedLeadCount)} qualified in loaded results`,
      },
      {
        label: 'Sessions',
        value: formatNumber(stats.sessions),
        hint: activeSessionName ? `Active: ${activeSessionName}` : 'Create or load a session',
      },
      {
        label: 'Runs',
        value: formatNumber(stats.runs),
        hint: latestRun?.finished_at ? `Last finished ${formatRelativeTime(latestRun.finished_at)}` : 'No completed runs yet',
      },
      {
        label: 'Visited URLs',
        value: formatNumber(stats.visited_urls),
        hint: stats.runs > 0 ? `${formatNumber(Math.round(stats.visited_urls / stats.runs))} avg per run` : 'No crawl history yet',
      },
    ],
    [activeSessionName, latestRun?.finished_at, qualifiedLeadCount, stats.leads, stats.runs, stats.sessions, stats.visited_urls],
  )

  const pipelineStages = useMemo<PipelineStage[]>(
    () => [
      {
        name: 'Search',
        count: formatNumber(configDraft.keywords.length),
        hint: 'Configured keywords available for the next scrape',
        tone: 'primary',
      },
      {
        name: 'Fetch',
        count: formatNumber(totalPagesInRecentRuns),
        hint: 'Pages crawled across recent runs',
        tone: 'info',
      },
      {
        name: 'Enrich',
        count: formatNumber(qualifiedLeadCount),
        hint: configDraft.ai_enrichment_enabled ? 'AI enrichment enabled for qualified rows' : 'AI enrichment is disabled',
        tone: configDraft.ai_enrichment_enabled ? 'success' : 'warning',
      },
      {
        name: 'Review',
        count: formatNumber(lowConfidenceCount),
        hint: 'Rows below the current confidence threshold',
        tone: 'warning',
      },
    ],
    [
      configDraft.ai_enrichment_enabled,
      configDraft.keywords.length,
      lowConfidenceCount,
      qualifiedLeadCount,
      totalPagesInRecentRuns,
    ],
  )

  const recentSessions = sessions.slice(0, 3)
  const currentSession = sessions.find((session) => session.id === activeSessionId) ?? null
  const totalLeadPages = Math.max(1, Math.ceil(leadResponse.total / Math.max(leadResponse.page_size, 1)))

  const runChatCommand = useCallback(
    async (message: string) => {
      if (!message.trim()) {
        return 'Usage: chat <message>'
      }

      setTerminalBusy(true)
      setTerminalError(null)
      setTerminalStatus('Sending message to AI...')
      const events = await collectSseEvents('/api/chat', {
        message,
        session_id: activeSessionId,
      })

      let reply = ''
      let resolvedSessionId = activeSessionId
      for (const event of events) {
        if (event.type === 'token') {
          setTerminalStatus('AI response streaming...')
          reply += String(event.content ?? '')
        }
        if (typeof event.session_id === 'number') {
          resolvedSessionId = event.session_id
        }
        if (event.type === 'error') {
          setTerminalError(String(event.content ?? 'Chat failed'))
          setTerminalStatus('Chat failed')
          setTerminalBusy(false)
          throw new Error(String(event.content ?? 'Chat failed'))
        }
      }

      await Promise.all([loadSessions(), loadStats()])
      if (resolvedSessionId) {
        setActiveSessionId(resolvedSessionId)
      }
      setTerminalStatus('AI response complete')
      setTerminalBusy(false)
      return reply || 'No response returned.'
    },
    [activeSessionId, loadSessions, loadStats],
  )

  const runScrapeCommand = useCallback(
    async (keywordsInput: string) => {
      const keywords = keywordsInput
        .split(',')
        .map((keyword) => keyword.trim())
        .filter(Boolean)
      const finalKeywords = keywords.length > 0 ? keywords : configDraft.keywords

      if (finalKeywords.length === 0) {
        return 'No keywords configured. Add keywords in Settings first.'
      }

      setTerminalBusy(true)
      setTerminalError(null)
      setTerminalStatus(`Starting scrape for ${finalKeywords.join(', ')}`)
      const events = await collectSseEvents('/api/scrape', {
        keywords: finalKeywords,
        session_id: activeSessionId,
        max_pages: configDraft.max_pages,
        target_new_leads: configDraft.target_new_leads,
      })

      const lines: string[] = [`scrape: ${finalKeywords.join(', ')}`]
      for (const event of events) {
        if (event.type === 'progress') {
          setTerminalStatus(String(event.msg ?? 'Scrape running...'))
        }
        if (event.type === 'lead') {
          const company = String(event.company_name ?? '?')
          const email = String(event.email ?? '-')
          const confidence = Number(event.confidence ?? 0).toFixed(2)
          lines.push(`lead: ${company} | ${email} | conf=${confidence}`)
          setTerminalStatus(`Found ${lines.filter((line) => line.startsWith('lead:')).length} new lead events`)
        }
        if (event.type === 'warning') {
          lines.push(`warning: ${String(event.msg ?? '')}`)
          setTerminalStatus(String(event.msg ?? 'Scrape warning'))
        }
        if (event.type === 'error') {
          setTerminalError(String(event.content ?? 'Scrape failed'))
          setTerminalStatus('Scrape failed')
          setTerminalBusy(false)
          throw new Error(String(event.content ?? 'Scrape failed'))
        }
        if (event.type === 'done') {
          setTerminalStatus(
            `Scrape complete: ${String(event.leads_new ?? 0)} new, ${String(event.leads_duplicate ?? 0)} dup, ${String(event.pages_visited ?? 0)} pages`,
          )
          lines.push(
            `done: new=${String(event.leads_new ?? 0)} dup=${String(event.leads_duplicate ?? 0)} discarded=${String(event.leads_discarded ?? 0)} pages=${String(event.pages_visited ?? 0)}`,
          )
        }
      }

      await reloadOperationalData()
      setTerminalBusy(false)
      return lines.slice(0, 14).join('\n')
    },
    [activeSessionId, configDraft.keywords, configDraft.max_pages, configDraft.target_new_leads, reloadOperationalData],
  )

  const executeTerminalTask = useCallback(
    async (task: () => Promise<string> | string) => {
      try {
        const result = await task()
        if (!terminalBusy) {
          setTerminalStatus('Ready')
        }
        return result
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Terminal action failed'
        setTerminalError(message)
        setTerminalStatus('Error')
        setTerminalBusy(false)
        return `Error: ${message}`
      }
    },
    [terminalBusy],
  )

  const terminalCommands = useMemo(
    () => ({
      help: () =>
        executeTerminalTask(() =>
          [
            'Plain text is sent to the AI chat endpoint.',
            '',
            'Commands:',
            'health',
            'stats',
            'sessions',
            'new [name]',
            'load <session_id>',
            'name <new name>',
            'history [limit]',
            'config',
            'leads [search]',
            'export',
            'dbinit',
            'dbreset',
            'chat <message>',
            'scrape [kw1, kw2]',
          ].join('\n'),
        ),
      health: async () =>
        executeTerminalTask(async () => {
          setTerminalStatus('Loading health...')
          const payload = await loadHealth()
          setTerminalStatus('Health loaded')
          return `status: ${payload.status}\ndatabase: ${payload.db ?? 'unknown'}\nleads: ${payload.leads ?? 0}\nruns: ${payload.runs ?? 0}`
        }),
      stats: async () =>
        executeTerminalTask(async () => {
          setTerminalStatus('Loading stats...')
          const payload = await loadStats()
          setTerminalStatus('Stats loaded')
          return [
            `leads: ${payload.leads}`,
            `visited_urls: ${payload.visited_urls}`,
            `runs: ${payload.runs}`,
            `sessions: ${payload.sessions}`,
          ].join('\n')
        }),
      sessions: async () =>
        executeTerminalTask(async () => {
          setTerminalStatus('Loading sessions...')
          const payload = await loadSessions()
          setTerminalStatus('Sessions loaded')
          if (payload.length === 0) {
            return 'No sessions found.'
          }
          return payload
            .map((session) => `#${session.id} ${session.name} | ${session.turn_count ?? 0} turns | ${formatRelativeTime(session.updated_at)}`)
            .join('\n')
        }),
      new: async (...parts: string[]) =>
        executeTerminalTask(async () => {
          setTerminalStatus('Creating session...')
          const session = await fetchJson<SessionRecord>('/api/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(parts.join(' ').trim() ? { name: parts.join(' ').trim() } : {}),
          })
          await loadSessions()
          setActiveSession(session)
          setTerminalStatus(`Created session #${session.id}`)
          return `Created session #${session.id} ${session.name}`
        }),
      load: async (sessionIdRaw: string) =>
        executeTerminalTask(async () => {
          const sessionId = Number.parseInt(sessionIdRaw, 10)
          if (Number.isNaN(sessionId)) {
            return 'Usage: load <session_id>'
          }
          setTerminalStatus(`Loading session #${sessionId}...`)
          const payload = await loadSessionHistory(sessionId)
          await loadSessions()
          const session = sessions.find((item) => item.id === sessionId) ?? { id: sessionId, name: `Session ${sessionId}` }
          setActiveSession(session)
          setTerminalStatus(`Loaded session #${sessionId}`)
          return `Loaded session #${sessionId} with ${payload.length} turns`
        }),
      name: async (...parts: string[]) =>
        executeTerminalTask(async () => {
          const nextName = parts.join(' ').trim()
          if (!activeSessionId || !nextName) {
            return 'Usage: name <new name>'
          }
          setTerminalStatus(`Renaming session #${activeSessionId}...`)
          await fetchJson<{ status: string }>(`/api/sessions/${activeSessionId}/rename`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: nextName }),
          })
          await loadSessions()
          setActiveSessionName(nextName)
          setRenameSessionValue(nextName)
          setTerminalStatus(`Renamed session #${activeSessionId}`)
          return `Renamed session #${activeSessionId} to ${nextName}`
        }),
      history: async (limitRaw = '10') =>
        executeTerminalTask(async () => {
          if (!activeSessionId) {
            return 'No active session.'
          }
          const limit = Number.parseInt(limitRaw, 10)
          setTerminalStatus(`Loading history for session #${activeSessionId}...`)
          const payload = await loadSessionHistory(activeSessionId, Number.isNaN(limit) ? 10 : limit)
          setTerminalStatus(`Loaded ${payload.length} turns`)
          if (payload.length === 0) {
            return 'No turns yet.'
          }
          return payload
            .map((turn) => {
              const preview = turn.content.length > 100 ? `${turn.content.slice(0, 100)}...` : turn.content
              const mode = turn.mode ? ` [${turn.mode}]` : ''
              return `${turn.role}${mode}: ${preview}`
            })
            .join('\n')
        }),
      config: async () =>
        executeTerminalTask(async () => {
          setTerminalStatus('Loading config...')
          const payload = await loadConfig()
          setTerminalStatus('Config loaded')
          return [
            `keywords: ${payload.keywords.join(', ') || '-'}`,
            `max_pages: ${payload.max_pages}`,
            `target_new_leads: ${payload.target_new_leads}`,
            `request_delay_seconds: ${payload.request_delay_seconds}`,
            `ai_enrichment_enabled: ${payload.ai_enrichment_enabled}`,
            `ai_confidence_threshold: ${payload.ai_confidence_threshold}`,
          ].join('\n')
        }),
      leads: async (...parts: string[]) =>
        executeTerminalTask(async () => {
          const query = parts.join(' ').trim()
          setTerminalStatus(`Loading leads${query ? ` for "${query}"` : ''}...`)
          const payload = await loadLeads(query, includeArchived, 1)
          setTerminalStatus(`Loaded ${payload.leads.length} leads`)
          if (payload.leads.length === 0) {
            return 'No leads found.'
          }
          return payload.leads
            .slice(0, 12)
            .map((lead) => `${lead.company_name ?? '-'} | ${buildContactName(lead)} | ${(lead.confidence ?? 0).toFixed(2)}`)
            .join('\n')
        }),
      export: () =>
        executeTerminalTask(() => {
          setTerminalStatus('Opening CSV export...')
          handleExportLeads()
          setTerminalStatus('CSV export opened')
          return 'Opened CSV export in a new tab.'
        }),
      dbinit: async () =>
        executeTerminalTask(async () => {
          setTerminalStatus('Initialising database...')
          const didInit = await handleDbInit()
          setTerminalStatus(didInit ? 'Database initialised' : 'Database initialisation failed')
          return didInit ? 'Database initialised.' : 'Database initialisation failed.'
        }),
      dbreset: async () =>
        executeTerminalTask(async () => {
          setTerminalStatus('Resetting database...')
          const didReset = await handleDbReset()
          setTerminalStatus(didReset ? 'Database reset complete' : 'Database reset cancelled')
          return didReset ? 'Database reset requested.' : 'Database reset cancelled.'
        }),
      chat: async (...parts: string[]) => executeTerminalTask(() => runChatCommand(parts.join(' ').trim())),
      scrape: async (...parts: string[]) => executeTerminalTask(() => runScrapeCommand(parts.join(' '))),
    }),
    [
      activeSessionId,
      executeTerminalTask,
      handleDbInit,
      handleDbReset,
      handleExportLeads,
      includeArchived,
      loadConfig,
      loadHealth,
      loadLeads,
      loadSessionHistory,
      loadSessions,
      loadStats,
      runChatCommand,
      runScrapeCommand,
      sessions,
      setActiveSession,
    ],
  )

  const apiChipLabel = dashboardLoading
    ? 'Checking API'
    : error
      ? 'API unavailable'
      : health?.status ?? 'unknown'

  const connectionColor = error ? 'error' : health?.status === 'ok' ? 'success' : 'default'
  const leadDrawerActionLabel = leadDetail?.archived ? 'Restore lead' : 'Archive lead'
  const handleNavigate = useCallback((nextTab: NavItem) => {
    setTab(nextTab)
    contentSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  return (
    <Box
      sx={{
        minHeight: '100vh',
        bgcolor: 'background.default',
        background:
          'radial-gradient(circle at top left, rgba(59,130,246,0.18), transparent 26%), radial-gradient(circle at top right, rgba(168,85,247,0.14), transparent 24%), linear-gradient(180deg, #09111f 0%, #070b14 100%)',
      }}
    >
      <AppBar
        position="fixed"
        elevation={0}
        sx={{
          borderBottom: '1px solid',
          borderColor: 'divider',
          bgcolor: 'rgba(9, 17, 31, 0.84)',
          backdropFilter: 'blur(16px)',
        }}
      >
        <Toolbar sx={{ gap: 2, minHeight: 64 }}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="overline" sx={{ color: 'primary.light', letterSpacing: 2 }}>
              LeadScraper Monster
            </Typography>
            <Typography variant="h6" sx={{ lineHeight: 1.1 }}>
              React dashboard rebuild
            </Typography>
          </Box>

          <Chip
            label={apiChipLabel}
            color={connectionColor}
            variant={error ? 'outlined' : 'filled'}
            sx={{ textTransform: 'capitalize' }}
          />
          <Button color="inherit" onClick={() => void refreshAll()}>
            Refresh
          </Button>
          <Button color="inherit" onClick={openSessionsDrawer}>
            Sessions
          </Button>
          <Button color="inherit" onClick={openSettingsDialog}>
            Settings
          </Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ display: 'flex' }}>
        <Drawer
          variant="permanent"
          sx={{
            display: { xs: 'none', md: 'block' },
            width: 280,
            flexShrink: 0,
            '& .MuiDrawer-paper': {
              width: 280,
              boxSizing: 'border-box',
              bgcolor: 'rgba(10, 16, 28, 0.7)',
              borderRightColor: 'divider',
              backdropFilter: 'blur(16px)',
            },
          }}
        >
          <Toolbar />
          <Box sx={{ p: 2, display: 'grid', gap: 2 }}>
            <Paper variant="outlined" sx={{ p: 1.75, bgcolor: 'rgba(255,255,255,0.02)' }}>
              <Typography variant="subtitle2" sx={{ color: 'text.secondary', mb: 1 }}>
                Navigation
              </Typography>
              <List dense disablePadding>
                {navItems.map((item) => (
                  <ListItem key={item} disablePadding sx={{ mb: 0.5 }}>
                    <ListItemButton selected={tab === item} onClick={() => handleNavigate(item)}>
                      <ListItemText primary={item} />
                    </ListItemButton>
                  </ListItem>
                ))}
              </List>
            </Paper>

            <Paper variant="outlined" sx={{ p: 1.75, bgcolor: 'rgba(255,255,255,0.02)' }}>
              <Typography variant="subtitle2" sx={{ color: 'text.secondary', mb: 1 }}>
                Runtime stats
              </Typography>
              <Stack spacing={1}>
                {summaryCards.map((metric) => (
                  <Paper
                    key={metric.label}
                    variant="outlined"
                    sx={{
                      p: 1.25,
                      bgcolor: 'rgba(255,255,255,0.02)',
                    }}
                  >
                    <Box sx={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 1 }}>
                      <Typography
                        variant="body2"
                        sx={{ color: 'text.secondary', fontWeight: 700, whiteSpace: 'nowrap' }}
                      >
                        {metric.label}
                      </Typography>
                      <Typography
                        variant="h6"
                        sx={{ fontWeight: 800, lineHeight: 1, whiteSpace: 'nowrap' }}
                      >
                        {metric.value}
                      </Typography>
                    </Box>
                    <Typography variant="caption" color="text.secondary">
                      {metric.hint}
                    </Typography>
                  </Paper>
                ))}
              </Stack>
            </Paper>
          </Box>
        </Drawer>

        <Box component="main" sx={{ flexGrow: 1, minWidth: 0, p: { xs: 2, md: 3 } }}>
          <Toolbar />

          <Stack spacing={3}>
            {(dashboardLoading || leadsLoading || savingSettings || working || sessionLoading) && (
              <LinearProgress sx={{ borderRadius: 999 }} />
            )}

            {error && (
              <Alert severity="error" variant="outlined">
                {error}
              </Alert>
            )}

            {notice && (
              <Alert severity="success" variant="outlined" onClose={() => setNotice(null)}>
                {notice}
              </Alert>
            )}

            <Paper
              variant="outlined"
              sx={{
                p: { xs: 2, md: 2.5 },
                bgcolor: 'rgba(13, 19, 34, 0.78)',
                backdropFilter: 'blur(16px)',
              }}
            >
              <Stack
                direction={{ xs: 'column', md: 'row' }}
                spacing={2.25}
                alignItems={{ xs: 'stretch', md: 'center' }}
                justifyContent="space-between"
              >
                <Box sx={{ maxWidth: 720 }}>
                  <Typography variant="overline" sx={{ color: 'primary.light', letterSpacing: 2 }}>
                    Dashboard shell
                  </Typography>
                  <Typography variant="h4" gutterBottom sx={{ fontSize: { xs: '2rem', md: '2.5rem' } }}>
                    THE MONSTER LEAD SCRAPER
                  </Typography>
                  <Typography color="text.secondary" sx={{ lineHeight: 1.6, maxWidth: 760 }}>
                    The rebuild now reads live health, stats, leads, sessions, runs, and config from the
                    Python API. Export, archive, rename, save, and terminal workflows all call the backend.
                  </Typography>
                </Box>

                <Stack direction="row" spacing={1} flexWrap="wrap" justifyContent="flex-end">
                  <Button variant="contained" onClick={openSettingsDialog}>
                    Open settings
                  </Button>
                  <Button variant="outlined" onClick={openSessionsDrawer}>
                    Open sessions
                  </Button>
                </Stack>
              </Stack>

              <Divider sx={{ my: 3 }} />

              <Box
                sx={{
                  display: 'grid',
                  gap: 2,
                  gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))', lg: 'repeat(4, minmax(0, 1fr))' },
                }}
              >
                {dashboardMetrics.map((metric) => (
                  <MetricTile key={metric.label} {...metric} />
                ))}
              </Box>
            </Paper>

            <Box
              sx={{
                display: 'grid',
                gap: 2,
                gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, 1fr) minmax(0, 2fr)' },
              }}
            >
              <Paper variant="outlined" sx={{ p: 2.25, bgcolor: 'rgba(255,255,255,0.02)' }}>
                <Stack spacing={2}>
                  <Stack
                    direction={{ xs: 'column', md: 'row' }}
                    spacing={1}
                    justifyContent="space-between"
                    alignItems={{ xs: 'stretch', md: 'center' }}
                  >
                    <Box>
                      <Typography variant="h6">Pipeline overview</Typography>
                      <Typography variant="body2" color="text.secondary">
                        Derived from live config, run history, and the currently loaded lead rows.
                      </Typography>
                    </Box>
                    <Chip
                      label={health?.db ? `DB: ${health.db}` : 'DB pending'}
                      variant="outlined"
                      sx={{ alignSelf: { xs: 'flex-start', md: 'auto' } }}
                    />
                  </Stack>

                  <Box
                    sx={{
                      display: 'grid',
                      gap: 2,
                      gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' },
                    }}
                  >
                    {pipelineStages.map((stage) => (
                      <MetricTile
                        key={stage.name}
                        label={stage.name}
                        value={stage.count}
                        note={stage.hint}
                        tone={stage.tone}
                        size="compact"
                      />
                    ))}
                  </Box>

                  <Paper variant="outlined" sx={{ p: 1.75, bgcolor: 'rgba(255,255,255,0.02)' }}>
                    <Stack spacing={1}>
                      <Typography variant="subtitle2" color="text.secondary">
                        Current focus
                      </Typography>
                      <Typography variant="body1">
                        {currentSession
                          ? `Session #${currentSession.id} "${currentSession.name}" is active with ${currentSession.turn_count ?? 0} turns.`
                          : 'No session is active. Create one from the drawer or terminal.'}
                      </Typography>
                    </Stack>
                  </Paper>
                </Stack>
              </Paper>

              <Paper variant="outlined" sx={{ p: 2.25, bgcolor: 'rgba(255,255,255,0.02)' }}>
                <Stack spacing={2}>
                  <Typography variant="h6">Terminal</Typography>
                  <Typography variant="body2" color="text.secondary">
                    Primary chat input for the AI assistant. Plain text goes to chat, while explicit commands stay available for operator actions.
                  </Typography>
                  <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} alignItems={{ xs: 'stretch', md: 'center' }}>
                    <Chip
                      label={terminalBusy ? 'AI working' : 'Ready'}
                      color={terminalBusy ? 'warning' : terminalError ? 'error' : 'success'}
                      variant={terminalBusy ? 'filled' : 'outlined'}
                      sx={{ alignSelf: { xs: 'flex-start', md: 'auto' } }}
                    />
                    <Typography variant="body2" color={terminalError ? 'error.main' : 'text.secondary'}>
                      {terminalError ?? terminalStatus}
                    </Typography>
                  </Stack>
                  {terminalBusy && <LinearProgress sx={{ borderRadius: 999 }} />}
                  <Box
                    sx={{
                      height: 480,
                      overflow: 'hidden',
                      borderRadius: 2,
                      '& .index_terminal__teubZ': {
                        width: '100%',
                        height: '100%',
                      },
                      '& .index_editor__JoDSg, & .index_editor__JoDSg *': {
                        fontSize: '14px',
                        lineHeight: 1.35,
                      },
                    }}
                  >
                    <ReactTerminal
                      commands={terminalCommands}
                      welcomeMessage={
                        'LeadScraper Monster terminal. Type a normal message to chat with the AI, or type help to inspect available commands.\n'
                      }
                      prompt="monster"
                      theme="dracula"
                      showControlBar
                      showControlButtons
                      defaultHandler={(command: string) => runChatCommand(command)}
                    />
                  </Box>
                </Stack>
              </Paper>
            </Box>

            <Paper ref={contentSectionRef} variant="outlined" sx={{ p: 2.25, bgcolor: 'rgba(255,255,255,0.02)' }}>
              <Tabs value={tab} onChange={(_, value: NavItem) => setTab(value)} sx={{ mb: 2 }}>
                {navItems.map((item) => (
                  <Tab key={item} label={item} value={item} />
                ))}
              </Tabs>

              {tab === 'Overview' && (
                <Stack spacing={2}>
                  <Alert severity={error ? 'error' : 'success'} variant="outlined">
                    {error
                      ? error
                      : dashboardLoading
                        ? 'Loading dashboard data...'
                        : `Loaded ${formatNumber(stats.leads)} leads, ${formatNumber(stats.sessions)} sessions, and ${formatNumber(stats.runs)} runs.`}
                  </Alert>

                  <Box
                    sx={{
                      display: 'grid',
                      gap: 2,
                      gridTemplateColumns: { xs: '1fr', md: 'repeat(3, minmax(0, 1fr))' },
                    }}
                  >
                    {recentSessions.map((session) => (
                      <Card
                        key={session.id}
                        variant="outlined"
                        sx={{ bgcolor: 'rgba(255,255,255,0.02)', cursor: 'pointer' }}
                        onClick={() => setActiveSession(session)}
                      >
                        <CardContent>
                          <Typography variant="subtitle2" color="text.secondary">
                            Session #{session.id}
                          </Typography>
                          <Typography variant="h6">{session.name}</Typography>
                          <Typography variant="body2" color="text.secondary">
                            {session.turn_count ?? 0} turns - updated {formatRelativeTime(session.updated_at)}
                          </Typography>
                        </CardContent>
                      </Card>
                    ))}
                  </Box>

                  <Paper variant="outlined" sx={{ p: 2, bgcolor: 'rgba(255,255,255,0.02)' }}>
                    <Stack spacing={1.5}>
                      <Typography variant="h6">Current session history</Typography>
                      <Typography variant="body2" color="text.secondary">
                        {currentSession
                          ? `Showing the latest turns for #${currentSession.id} ${currentSession.name}`
                          : 'No active session selected.'}
                      </Typography>
                      {sessionHistory.length === 0 ? (
                        <Typography variant="body2" color="text.secondary">
                          No turns yet.
                        </Typography>
                      ) : (
                        <Stack spacing={1}>
                          {sessionHistory.slice(-6).map((turn, index) => (
                            <Paper
                              key={`${turn.role}-${index}-${turn.created_at ?? ''}`}
                              variant="outlined"
                              sx={{ p: 1.25, bgcolor: 'rgba(255,255,255,0.02)' }}
                            >
                              <Typography variant="caption" color="text.secondary">
                                {turn.role}
                                {turn.mode ? ` - ${turn.mode}` : ''}
                                {turn.created_at ? ` - ${formatRelativeTime(turn.created_at)}` : ''}
                              </Typography>
                              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                                {turn.content}
                              </Typography>
                            </Paper>
                          ))}
                        </Stack>
                      )}
                    </Stack>
                  </Paper>

                  <Paper variant="outlined" sx={{ p: 2, bgcolor: 'rgba(255,255,255,0.02)' }}>
                    <Stack spacing={1.5}>
                      <Typography variant="h6">Recent runs</Typography>
                      <Typography variant="body2" color="text.secondary">
                        Live scrape history from `GET /api/runs`.
                      </Typography>
                      {runs.length === 0 ? (
                        <Typography variant="body2" color="text.secondary">
                          No runs recorded yet.
                        </Typography>
                      ) : (
                        <Stack spacing={1}>
                          {runs.slice(0, 5).map((run) => (
                            <Paper
                              key={run.id}
                              variant="outlined"
                              sx={{ p: 1.25, bgcolor: 'rgba(255,255,255,0.02)' }}
                            >
                              <Stack
                                direction={{ xs: 'column', md: 'row' }}
                                justifyContent="space-between"
                                spacing={1}
                              >
                                <Box>
                                  <Typography variant="subtitle2">
                                    Run #{run.id} {run.keywords && run.keywords.length > 0 ? `- ${run.keywords.join(', ')}` : ''}
                                  </Typography>
                                  <Typography variant="body2" color="text.secondary">
                                    {formatNumber(run.pages_crawled)} pages - {formatNumber(run.leads_new)} new -{' '}
                                    {formatNumber(run.leads_duplicate)} dup - {formatNumber(run.leads_discarded)} discarded
                                  </Typography>
                                </Box>
                                <Typography variant="caption" color="text.secondary">
                                  {formatRelativeTime(run.finished_at ?? run.created_at)}
                                </Typography>
                              </Stack>
                            </Paper>
                          ))}
                        </Stack>
                      )}
                    </Stack>
                  </Paper>
                </Stack>
              )}

              {tab === 'Leads' && (
                <Stack spacing={2}>
                  <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} justifyContent="space-between">
                    <TextField
                      fullWidth
                      label="Search leads"
                      value={search}
                      onChange={(event) => {
                        setSearch(event.target.value)
                        setLeadPage(1)
                      }}
                      placeholder="company, contact, category, email..."
                    />
                    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                      <FormControlLabel
                        control={
                          <Switch
                            checked={includeArchived}
                            onChange={(event) => {
                              setIncludeArchived(event.target.checked)
                              setLeadPage(1)
                            }}
                          />
                        }
                        label="Include archived"
                      />
                      <Button variant="outlined" onClick={handleExportLeads}>
                        Export CSV
                      </Button>
                    </Stack>
                  </Stack>

                  <Typography variant="body2" color="text.secondary">
                    Showing page {leadResponse.page} of {totalLeadPages} - {leadResponse.leads.length} of {leadResponse.total} matching leads.
                  </Typography>

                  <TableContainer component={Paper} variant="outlined" sx={{ bgcolor: 'rgba(255,255,255,0.02)' }}>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>Company</TableCell>
                          <TableCell>Contact</TableCell>
                          <TableCell>Role</TableCell>
                          <TableCell>Email</TableCell>
                          <TableCell>Country</TableCell>
                          <TableCell>Category</TableCell>
                          <TableCell>Confidence</TableCell>
                          <TableCell>Status</TableCell>
                          <TableCell align="right">Action</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {leadResponse.leads.map((lead) => (
                          <TableRow hover key={lead.id} sx={{ cursor: 'pointer' }} onClick={() => openLeadDetails(lead)}>
                            <TableCell>{lead.company_name ?? '-'}</TableCell>
                            <TableCell>{buildContactName(lead)}</TableCell>
                            <TableCell>{lead.role ?? lead.title ?? '-'}</TableCell>
                            <TableCell>{lead.email ?? '-'}</TableCell>
                            <TableCell>{lead.country ?? '-'}</TableCell>
                            <TableCell>{lead.category ?? '-'}</TableCell>
                            <TableCell>{leadConfidence(lead).toFixed(2)}</TableCell>
                            <TableCell onClick={(event) => event.stopPropagation()}>
                              {lead.archived ? (
                                <Typography variant="body2" color="text.secondary">
                                  Archived
                                </Typography>
                              ) : (
                                <TextField
                                  select
                                  size="small"
                                  variant="standard"
                                  value={lead.status ?? 'New'}
                                  onChange={(event) => {
                                    void handleLeadPatch(lead.id, { status: event.target.value }, `Updated lead #${lead.id} status`)
                                  }}
                                  sx={{ minWidth: 120 }}
                                >
                                  {leadStatusOptions.map((status) => (
                                    <MenuItem key={status} value={status}>
                                      {status}
                                    </MenuItem>
                                  ))}
                                </TextField>
                              )}
                            </TableCell>
                            <TableCell align="right">
                              <Button
                                size="small"
                                variant="outlined"
                                onClick={(event) => {
                                  event.stopPropagation()
                                  void handleArchiveToggle(lead)
                                }}
                              >
                                {lead.archived ? 'Restore' : 'Archive'}
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                        {leadResponse.leads.length === 0 && (
                          <TableRow>
                            <TableCell colSpan={9}>
                              <Typography variant="body2" color="text.secondary">
                                No leads match the current filters.
                              </Typography>
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </TableContainer>

                  <Stack direction="row" justifyContent="center">
                    <Pagination
                      color="primary"
                      page={leadPage}
                      count={totalLeadPages}
                      onChange={(_, value) => setLeadPage(value)}
                    />
                  </Stack>
                </Stack>
              )}

              {tab === 'Terminal' && (
                <Stack spacing={2}>
                  <Typography variant="body2" color="text.secondary">
                    Use the terminal panel above. Commands now execute against the backend rather than mocked local data.
                  </Typography>
                  <Paper variant="outlined" sx={{ p: 2, bgcolor: 'rgba(255,255,255,0.02)' }}>
                    <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
                      Terminal behavior
                    </Typography>
                    <Typography variant="body2" sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                      summarize the current dashboard state{'\n'}what changed after the last scrape?{'\n'}help{'\n'}stats{'\n'}sessions{'\n'}new q2 dental{'\n'}load 12{'\n'}name UK operators{'\n'}config{'\n'}leads logistics{'\n'}export{'\n'}dbinit{'\n'}dbreset{'\n'}scrape dental clinics london
                    </Typography>
                  </Paper>
                </Stack>
              )}

              {tab === 'Settings' && (
                <Box
                  sx={{
                    display: 'grid',
                    gap: 2,
                    gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' },
                  }}
                >
                  <Card variant="outlined" sx={{ bgcolor: 'rgba(255,255,255,0.02)' }}>
                    <CardContent>
                      <Typography variant="h6" gutterBottom>
                        Scrape defaults
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {configDraft.keywords.length} keywords configured, max {configDraft.max_pages} pages per run,
                        target {configDraft.target_new_leads} new leads.
                      </Typography>
                    </CardContent>
                  </Card>
                  <Card variant="outlined" sx={{ bgcolor: 'rgba(255,255,255,0.02)' }}>
                    <CardContent>
                      <Typography variant="h6" gutterBottom>
                        AI enrichment
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {configDraft.ai_enrichment_enabled ? 'Enabled' : 'Disabled'} with confidence threshold{' '}
                        {configDraft.ai_confidence_threshold}.
                      </Typography>
                    </CardContent>
                  </Card>
                  <Card variant="outlined" sx={{ bgcolor: 'rgba(255,255,255,0.02)' }}>
                    <CardContent>
                      <Typography variant="h6" gutterBottom>
                        Session behavior
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        Current active session: {currentSession ? `#${currentSession.id} ${currentSession.name}` : 'none'}.
                      </Typography>
                    </CardContent>
                  </Card>
                  <Card variant="outlined" sx={{ bgcolor: 'rgba(255,255,255,0.02)' }}>
                    <CardContent>
                      <Typography variant="h6" gutterBottom>
                        Exports and archive
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        CSV export is live from the Leads tab. Archive and restore actions persist to the API.
                      </Typography>
                    </CardContent>
                  </Card>
                  <Card variant="outlined" sx={{ bgcolor: 'rgba(255,255,255,0.02)' }}>
                    <CardContent>
                      <Typography variant="h6" gutterBottom>
                        Admin actions
                      </Typography>
                      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                        <Button variant="outlined" onClick={() => void handleDbInit()}>
                          Init DB
                        </Button>
                        <Button color="warning" variant="outlined" onClick={() => void handleDbReset()}>
                          Reset DB
                        </Button>
                        <Button variant="outlined" onClick={handleExportLeads}>
                          Export CSV
                        </Button>
                      </Stack>
                    </CardContent>
                  </Card>
                </Box>
              )}
            </Paper>
          </Stack>
        </Box>
      </Box>

      <Drawer anchor="right" open={sessionsOpen} onClose={closeSessionsDrawer}>
        <Box sx={{ width: 380, p: 2 }}>
          <Stack spacing={2}>
            <Typography variant="h6">Sessions</Typography>
            <Typography variant="body2" color="text.secondary">
              Create, load, rename, and inspect session history from the live session store.
            </Typography>
            <Divider />

            <Stack direction="row" spacing={1}>
              <TextField
                fullWidth
                size="small"
                label="New session name"
                value={newSessionName}
                onChange={(event) => setNewSessionName(event.target.value)}
              />
              <Button variant="contained" onClick={() => void handleCreateSession()}>
                Create
              </Button>
            </Stack>

            <Stack direction="row" spacing={1}>
              <TextField
                fullWidth
                size="small"
                label="Rename active session"
                value={renameSessionValue}
                onChange={(event) => setRenameSessionValue(event.target.value)}
                disabled={!activeSessionId}
              />
              <Button variant="outlined" onClick={() => void handleRenameSession()} disabled={!activeSessionId}>
                Rename
              </Button>
            </Stack>

            <List disablePadding>
              {sessions.map((session) => (
                <ListItem key={session.id} disablePadding sx={{ mb: 0.5 }}>
                  <ListItemButton selected={session.id === activeSessionId} onClick={() => setActiveSession(session)}>
                    <ListItemText
                      primary={`#${session.id} ${session.name}`}
                      secondary={`${session.turn_count ?? 0} turns - ${formatRelativeTime(session.updated_at)}`}
                    />
                  </ListItemButton>
                </ListItem>
              ))}
            </List>

            <Divider />

            <Typography variant="subtitle2" color="text.secondary">
              History preview
            </Typography>
            <Stack spacing={1}>
              {sessionHistory.slice(-5).map((turn, index) => (
                <Paper
                  key={`${turn.role}-${index}-${turn.created_at ?? ''}`}
                  variant="outlined"
                  sx={{ p: 1.25, bgcolor: 'rgba(255,255,255,0.02)' }}
                >
                  <Typography variant="caption" color="text.secondary">
                    {turn.role}
                    {turn.mode ? ` - ${turn.mode}` : ''}
                  </Typography>
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                    {turn.content}
                  </Typography>
                </Paper>
              ))}
              {sessionHistory.length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  No turns loaded for this session.
                </Typography>
              )}
            </Stack>
          </Stack>
        </Box>
      </Drawer>

      <Drawer anchor="bottom" open={Boolean(leadDetail)} onClose={closeLeadDrawer}>
        <Box
          sx={{ p: 2, maxWidth: 960, mx: 'auto', width: '100%' }}
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              closeLeadDrawer()
            }
          }}
        >
          <Stack spacing={2}>
            <Stack direction={{ xs: 'column', md: 'row' }} justifyContent="space-between" spacing={2}>
              <Box>
                <Typography variant="h6">Lead details</Typography>
                <Typography variant="body2" color="text.secondary">
                  Inspect the selected lead, edit its status and notes, and archive or restore it from here.
                </Typography>
              </Box>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                {leadDetail && (
                  <>
                    <Button
                      variant="contained"
                      onClick={() =>
                        void handleLeadPatch(
                          leadDetail.id,
                          {
                            status: leadDetail.status ?? 'New',
                            notes: leadDetail.notes ?? '',
                          },
                          `Saved lead #${leadDetail.id}`,
                        )
                      }
                    >
                      Save
                    </Button>
                    <Button variant="outlined" onClick={() => void handleArchiveToggle(leadDetail)}>
                      {leadDrawerActionLabel}
                    </Button>
                  </>
                )}
                <Button variant="text" onClick={closeLeadDrawer}>
                  Close
                </Button>
              </Stack>
            </Stack>
            {leadDetail ? (
              <Box
                sx={{
                  display: 'grid',
                  gap: 1.5,
                  gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' },
                }}
              >
                {[
                  ['Company', leadDetail.company_name ?? '-'],
                  ['Contact', buildContactName(leadDetail)],
                  ['Email', leadDetail.email ?? '-'],
                  ['Phone', leadDetail.phone ?? '-'],
                  ['Role', leadDetail.role ?? leadDetail.title ?? '-'],
                  ['Category', leadDetail.category ?? '-'],
                  ['City', leadDetail.city ?? '-'],
                  ['Country', leadDetail.country ?? '-'],
                  ['Confidence', leadConfidence(leadDetail).toFixed(2)],
                  ['Website', leadDetail.website ?? '-'],
                  ['Owner', leadDetail.owner ?? '-'],
                  ['Last touch', leadDetail.last_touch ?? '-'],
                ].map(([label, value]) => (
                  <Paper key={label} variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" color="text.secondary">
                      {label}
                    </Typography>
                    <Typography variant="body1" sx={{ wordBreak: 'break-word' }}>
                      {value}
                    </Typography>
                  </Paper>
                ))}
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
                    Status
                  </Typography>
                  {leadDetail.archived ? (
                    <Typography variant="body1">Archived</Typography>
                  ) : (
                    <TextField
                      select
                      fullWidth
                      value={leadDetail.status ?? 'New'}
                      onChange={(event) =>
                        setLeadDetail((current) => (current ? { ...current, status: event.target.value } : current))
                      }
                    >
                      {leadStatusOptions.map((status) => (
                        <MenuItem key={status} value={status}>
                          {status}
                        </MenuItem>
                      ))}
                    </TextField>
                  )}
                </Paper>
                <Paper variant="outlined" sx={{ p: 2, gridColumn: { xs: 'auto', md: '1 / -1' } }}>
                  <Typography variant="subtitle2" color="text.secondary">
                    Notes
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    minRows={4}
                    value={leadDetail.notes ?? ''}
                    placeholder="Add research notes, outreach context, or qualification details"
                    onChange={(event) =>
                      setLeadDetail((current) => (current ? { ...current, notes: event.target.value } : current))
                    }
                  />
                </Paper>
                <Paper variant="outlined" sx={{ p: 2, gridColumn: { xs: 'auto', md: '1 / -1' } }}>
                  <Typography variant="subtitle2" color="text.secondary">
                    Source URL
                  </Typography>
                  <Typography variant="body1" sx={{ wordBreak: 'break-word' }}>
                    {leadDetail.source_url ?? '-'}
                  </Typography>
                </Paper>
              </Box>
            ) : (
              <Typography color="text.secondary">Select a lead to inspect it here.</Typography>
            )}
          </Stack>
        </Box>
      </Drawer>

      <Dialog open={settingsOpen} onClose={closeSettingsDialog} fullWidth maxWidth="md">
        <DialogTitle>Settings</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Typography variant="body2" color="text.secondary">
              This dialog reads and writes the live configuration stored by the Python API.
            </Typography>
            <Box
              sx={{
                display: 'grid',
                gap: 2,
                gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' },
              }}
            >
              <TextField
                label="Default keywords"
                multiline
                minRows={6}
                value={configDraft.keywords.join('\n')}
                onChange={(event) =>
                  setConfigDraft((current) => ({
                    ...current,
                    keywords: event.target.value
                      .split('\n')
                      .map((keyword) => keyword.trim())
                      .filter(Boolean),
                  }))
                }
              />
              <Stack spacing={2}>
                <TextField
                  label="Max pages"
                  type="number"
                  value={configDraft.max_pages}
                  onChange={(event) =>
                    setConfigDraft((current) => ({
                      ...current,
                      max_pages: Number.parseInt(event.target.value, 10) || 0,
                    }))
                  }
                />
                <TextField
                  label="Target new leads"
                  type="number"
                  value={configDraft.target_new_leads}
                  onChange={(event) =>
                    setConfigDraft((current) => ({
                      ...current,
                      target_new_leads: Number.parseInt(event.target.value, 10) || 0,
                    }))
                  }
                />
                <TextField
                  label="Request delay (seconds)"
                  type="number"
                  value={configDraft.request_delay_seconds}
                  onChange={(event) =>
                    setConfigDraft((current) => ({
                      ...current,
                      request_delay_seconds: Number.parseFloat(event.target.value) || 0,
                    }))
                  }
                />
                <TextField
                  label="AI confidence threshold"
                  type="number"
                  value={configDraft.ai_confidence_threshold}
                  onChange={(event) =>
                    setConfigDraft((current) => ({
                      ...current,
                      ai_confidence_threshold: Number.parseFloat(event.target.value) || 0,
                    }))
                  }
                />
                <FormControlLabel
                  control={
                    <Switch
                      checked={configDraft.ai_enrichment_enabled}
                      onChange={(event) =>
                        setConfigDraft((current) => ({
                          ...current,
                          ai_enrichment_enabled: event.target.checked,
                        }))
                      }
                    />
                  }
                  label="AI enrichment enabled"
                />
              </Stack>
            </Box>
            <Divider />
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
              <Button variant="outlined" onClick={() => void handleDbInit()}>
                Init DB
              </Button>
              <Button color="warning" variant="outlined" onClick={() => void handleDbReset()}>
                Reset DB
              </Button>
              <Button variant="outlined" onClick={handleExportLeads}>
                Export CSV
              </Button>
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeSettingsDialog}>Close</Button>
          <Button onClick={() => void loadConfig()}>Reload</Button>
          <Button variant="contained" onClick={() => void handleSaveSettings()} disabled={savingSettings}>
            Save settings
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

export default App
