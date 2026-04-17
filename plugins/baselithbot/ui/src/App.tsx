import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Overview } from "./pages/Overview";
import { RunTask } from "./pages/RunTask";
import { Sessions } from "./pages/Sessions";
import { Channels } from "./pages/Channels";
import { Skills } from "./pages/Skills";
import { Crons } from "./pages/Crons";
import { Nodes } from "./pages/Nodes";
import { Doctor } from "./pages/Doctor";
import { Logs } from "./pages/Logs";
import { NotFound } from "./pages/NotFound";

export default function App() {
  return (
    <Layout>
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
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Layout>
  );
}
