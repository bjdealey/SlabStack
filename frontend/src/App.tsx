import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/AppShell'
import { Dashboard } from '@/pages/Dashboard'
import { Collection } from '@/pages/Collection'
import { CardDetail } from '@/pages/CardDetail'
import { Settings } from '@/pages/Settings'
import { AnalyticsPage, SubmissionsPage } from '@/pages/PhasePlaceholder'

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/collection" element={<Collection />} />
        <Route path="/cards/:cardId" element={<CardDetail />} />
        <Route path="/submissions" element={<SubmissionsPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  )
}
