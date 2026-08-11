import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/AppShell'
import { Dashboard } from '@/pages/Dashboard'
import { Collection } from '@/pages/Collection'
import { CardDetail } from '@/pages/CardDetail'
import { Settings } from '@/pages/Settings'
import { Analytics } from '@/pages/Analytics'
import { Submissions } from '@/pages/Submissions'

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/collection" element={<Collection />} />
        <Route path="/cards/:cardId" element={<CardDetail />} />
        <Route path="/submissions" element={<Submissions />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  )
}
