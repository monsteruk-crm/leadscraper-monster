import { useEffect, useMemo, useState } from 'react'
import './App.css'

type HealthResponse = {
  status: string
  db?: string
  detail?: string
}

type Metric = {
  label: string
  value: string
  hint: string
}

type LeadStub = {
  company: string
  contact: string
  role: string
  confidence: string
  status: string
}

const metrics: Metric[] = [
  { label: 'Leads', value: '124', hint: '23 new this week' },
  { label: 'Sessions', value: '18', hint: '5 active conversations' },
  { label: 'Runs', value: '41', hint: 'Last run 12 minutes ago' },
  { label: 'Visited URLs', value: '1,284', hint: '2 blocked by robots.txt' },
]

const recentLeads: LeadStub[] = [
  {
    company: 'Northstar Logistics',
    contact: 'Maya Chen',
    role: 'Operations Director',
    confidence: '0.91',
    status: 'Qualified',
  },
  {
    company: 'Horizon Dental Group',
    contact: 'James Patel',
    role: 'Practice Manager',
    confidence: '0.78',
    status: 'Reviewing',
  },
  {
    company: 'Blue Peak Advisory',
    contact: 'Sofia Alvarez',
    role: 'Founder',
    confidence: '0.63',
    status: 'Queued',
  },
]

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    async function loadHealth() {
      try {
        setLoading(true)
        setError(null)
        const response = await fetch('/api/health', {
          signal: controller.signal,
        })

        if (!response.ok) {
          throw new Error(`Request failed with ${response.status}`)
        }

        const payload = (await response.json()) as HealthResponse
        setHealth(payload)
      } catch (err) {
        if (controller.signal.aborted) {
          return
        }
        const message = err instanceof Error ? err.message : 'Unknown error'
        setError(message)
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      }
    }

    void loadHealth()

    return () => controller.abort()
  }, [])

  const connectionState = useMemo(() => {
    if (loading) {
      return 'Checking API connection'
    }

    if (error) {
      return 'API unavailable'
    }

    if (health?.status === 'ok') {
      return 'API connected'
    }

    return 'API status unknown'
  }, [error, health, loading])

  return (
    <main className="dashboard-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">LeadScraper Monster</p>
          <h1>React dashboard skeleton</h1>
        </div>
        <div className="status-pill">{connectionState}</div>
      </header>

      <section className="hero-grid">
        <article className="panel panel-feature">
          <div className="panel-header">
            <span>System</span>
            <span className="panel-chip">frontend</span>
          </div>
          <h2>Move the UI into React without touching the API yet.</h2>
          <p>
            This screen is a static scaffold for the new dashboard. It only
            calls one endpoint: <code>/api/health</code>.
          </p>
          <div className="feature-list">
            <div>
              <strong>Scope</strong>
              <span>Layout, navigation, data cards, and table shell</span>
            </div>
            <div>
              <strong>Backend</strong>
              <span>Python stays as the API layer for now</span>
            </div>
          </div>
        </article>

        <article className="panel panel-health">
          <div className="panel-header">
            <span>API Check</span>
            <span className={`health-chip ${health?.status === 'ok' ? 'ok' : 'warn'}`}>
              {health?.status ?? (loading ? 'loading' : 'error')}
            </span>
          </div>
          <div className="health-body">
            <p className="health-label">Health endpoint</p>
            <h2>{loading ? 'Loading...' : error ? 'Not reachable' : 'Live response received'}</h2>
            <p className="health-detail">
              {error
                ? error
                : health?.db
                  ? `Database: ${health.db}`
                  : 'Waiting for the first response from the API.'}
            </p>
          </div>
        </article>
      </section>

      <section className="metrics-grid" aria-label="Dashboard metrics">
        {metrics.map((metric) => (
          <article className="panel metric-card" key={metric.label}>
            <span className="metric-label">{metric.label}</span>
            <strong className="metric-value">{metric.value}</strong>
            <span className="metric-hint">{metric.hint}</span>
          </article>
        ))}
      </section>

      <section className="content-grid">
        <article className="panel table-panel">
          <div className="panel-header">
            <span>Recent leads</span>
            <span className="panel-chip">Mock data</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Contact</th>
                  <th>Role</th>
                  <th>Confidence</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {recentLeads.map((lead) => (
                  <tr key={lead.company}>
                    <td>{lead.company}</td>
                    <td>{lead.contact}</td>
                    <td>{lead.role}</td>
                    <td>{lead.confidence}</td>
                    <td>{lead.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>

        <article className="panel activity-panel">
          <div className="panel-header">
            <span>Work queue</span>
            <span className="panel-chip">Skeleton</span>
          </div>
          <ul className="activity-list">
            <li>
              <strong>Keyword batches</strong>
              <span>Ready for search presets and filters.</span>
            </li>
            <li>
              <strong>Scrape runs</strong>
              <span>Will show progress once the scraper UI is ported.</span>
            </li>
            <li>
              <strong>Session timeline</strong>
              <span>Reserved for the conversation history sidebar.</span>
            </li>
          </ul>
        </article>
      </section>
    </main>
  )
}

export default App
