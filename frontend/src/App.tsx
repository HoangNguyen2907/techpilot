import { Navigate, Route, Routes } from 'react-router-dom'
import { ChatShell } from '@/components/chat/ChatShell'
import { LoginPage } from '@/pages/LoginPage'
import { ProtectedRoute } from '@/router'

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/*" element={<ChatShell />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
