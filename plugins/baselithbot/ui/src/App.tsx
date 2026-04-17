import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Skeleton } from './components/Skeleton';

const Overview = lazy(() => import('./pages/Overview').then((m) => ({ default: m.Overview })));
const RunTask = lazy(() => import('./pages/RunTask').then((m) => ({ default: m.RunTask })));
const Sessions = lazy(() => import('./pages/Sessions').then((m) => ({ default: m.Sessions })));
const Channels = lazy(() => import('./pages/Channels').then((m) => ({ default: m.Channels })));
const Skills = lazy(() => import('./pages/Skills').then((m) => ({ default: m.Skills })));
const Crons = lazy(() => import('./pages/Crons').then((m) => ({ default: m.Crons })));
const Nodes = lazy(() => import('./pages/Nodes').then((m) => ({ default: m.Nodes })));
const Doctor = lazy(() => import('./pages/Doctor').then((m) => ({ default: m.Doctor })));
const Logs = lazy(() => import('./pages/Logs').then((m) => ({ default: m.Logs })));
const Agents = lazy(() => import('./pages/Agents').then((m) => ({ default: m.Agents })));
const Workspaces = lazy(() =>
  import('./pages/Workspaces').then((m) => ({ default: m.Workspaces }))
);
const Metrics = lazy(() => import('./pages/Metrics').then((m) => ({ default: m.Metrics })));
const Canvas = lazy(() => import('./pages/Canvas').then((m) => ({ default: m.Canvas })));
const Models = lazy(() => import('./pages/Models').then((m) => ({ default: m.Models })));
const NotFound = lazy(() => import('./pages/NotFound').then((m) => ({ default: m.NotFound })));

function PageFallback() {
  return (
    <div className="grid grid-cols-4" aria-busy="true">
      {Array.from({ length: 8 }).map((_, i) => (
        <Skeleton key={i} height={112} />
      ))}
    </div>
  );
}

export default function App() {
  return (
    <Layout>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/run" element={<RunTask />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/channels" element={<Channels />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/crons" element={<Crons />} />
          <Route path="/nodes" element={<Nodes />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/doctor" element={<Doctor />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/workspaces" element={<Workspaces />} />
          <Route path="/metrics" element={<Metrics />} />
          <Route path="/canvas" element={<Canvas />} />
          <Route path="/models" element={<Models />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </Layout>
  );
}
