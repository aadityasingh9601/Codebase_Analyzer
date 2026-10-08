import { AppShell } from "@/components/app-shell";
import { ApiKeySettings } from "@/components/ApiKeySettings";

export const metadata = {
  title: "API keys",
  description: "Bring your own AI provider key, encrypted in your browser.",
};

export default function ApiKeysPage() {
  return (
    <AppShell>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <ApiKeySettings />
      </div>
    </AppShell>
  );
}