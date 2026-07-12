import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./pages/AppShell";
import LoginPage from "./pages/LoginPage";
import { PasswordSetupPage, TotpSetupPage } from "./pages/SetupPages";
import { DashboardPage, JobsPage, SubscriptionPage } from "./pages/UserPages";
import { BillingAdminPage } from "./pages/BillingAdminPage";
import BotBuilderPage from "./pages/BotBuilderPage";
import {
  PackagesAdminPage,
  SecurityAdminPage,
  UsersAdminPage,
  WorkersAdminPage,
} from "./pages/AdminPages";
import { ProxiesAdminPage } from "./pages/ProxyPoolsPage";
import { ScrapeAdminPage } from "./pages/ScrapeProfilesPage";
import { CaptchaAdminPage } from "./pages/CaptchaAdminPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/setup/password" element={<PasswordSetupPage />} />
        <Route path="/setup/2fa" element={<TotpSetupPage />} />
        <Route path="/app" element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="jobs" element={<JobsPage />} />
          <Route path="subscription" element={<SubscriptionPage />} />
          <Route path="admin/users" element={<UsersAdminPage />} />
          <Route path="admin/packages" element={<PackagesAdminPage />} />
          <Route path="admin/billing" element={<BillingAdminPage />} />
          <Route path="admin/proxies" element={<ProxiesAdminPage />} />
          <Route path="admin/workers" element={<WorkersAdminPage />} />
          <Route path="admin/scrape" element={<ScrapeAdminPage />} />
          <Route path="admin/captcha" element={<CaptchaAdminPage />} />
          <Route path="admin/security" element={<SecurityAdminPage />} />
          <Route path="admin/bot" element={<BotBuilderPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
