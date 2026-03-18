import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material'
import { Analytics } from '@vercel/analytics/react'
import { TerminalContextProvider } from 'react-terminal'
import App from './App.tsx'
import './index.css'

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#7c3aed',
      light: '#c4b5fd',
    },
    background: {
      default: '#070b14',
      paper: '#0d1322',
    },
  },
  shape: {
    borderRadius: 14,
  },
  typography: {
    fontFamily: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'].join(','),
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <TerminalContextProvider>
        <App />
        <Analytics />
      </TerminalContextProvider>
    </ThemeProvider>
  </StrictMode>,
)
