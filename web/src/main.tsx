import { lazy, StrictMode, Suspense, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import './index.css'
import Layout from './components/Layout'
import { syncRole } from './api'
const CandidateDetail = lazy(() => import('./pages/CandidateDetail'))
const Candidates = lazy(() => import('./pages/Candidates'))
const MapPage = lazy(() => import('./pages/MapPage'))
const Overview = lazy(() => import('./pages/Overview'))
const QA = lazy(() => import('./pages/QA'))
const Runs = lazy(() => import('./pages/Runs'))

const page = (el: ReactNode) => <Suspense fallback={<div className="p-8 text-ink-600">불러오는 중…</div>}>{el}</Suspense>

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: page(<Overview />) },
      { path: '/map', element: page(<MapPage />) },
      { path: '/candidates', element: page(<Candidates />) },
      { path: '/candidates/:id', element: page(<CandidateDetail />) },
      { path: '/qa', element: page(<QA />) },
      { path: '/runs', element: page(<Runs />) },
    ],
  },
])

void syncRole()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
