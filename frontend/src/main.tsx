import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { SessionGate } from './api/session'
import { App } from './App'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <BrowserRouter><SessionGate><App /></SessionGate></BrowserRouter>,
)
