"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertCircleIcon,
  KeyIcon,
  SpinnerIcon,
  TrashIcon,
} from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Notice } from "@/components/ui/notice";
import {
  PROVIDER_LABELS,
  PROVIDERS,
  clearActiveProvider,
  setActiveProvider,
  type ProviderId,
} from "@/lib/api-key-store";
import { KEY_PATTERNS, validateApiKey } from "@/lib/validateApiKey";
import { useActiveProvider } from "@/hooks/use-active-provider";
import {
  deleteApiKey,
  listSavedApiKeys,
  saveApiKey,
  type SavedApiKey,
} from "@/app/lib/actions/apiKeys";

/**
 * Manage "bring your own" AI provider keys.
 *
 * The plaintext key travels from this browser to the backend once, over HTTPS.
 * The server derives a per-user encryption key (PBKDF2 over `user_id +
 * MASTER_SECRET`, computed fresh and never stored), encrypts the key with
 * AES-256-GCM, and stores only the blob + a last-4 hint. The plaintext never
 * lives in this browser afterwards: this tab only remembers which saved
 * provider to ask with, and /chat sends just that id — the backend decrypts
 * the key server-side for each request.
 */
export function ApiKeySettings() {
  const activeProvider = useActiveProvider();

  const [saved, setSaved] = useState<SavedApiKey[]>([]);
  const [loadingKeys, setLoadingKeys] = useState(true);
  const [loadError, setLoadError] = useState("");

  // Add/replace form.
  const [provider, setProvider] = useState<ProviderId>("openrouter");
  const [keyValue, setKeyValue] = useState("");
  const [saving, setSaving] = useState(false);

  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const { valid: keyFormatValid, error: formatError } = validateApiKey(provider, keyValue);

  const refresh = useCallback(() => {
    listSavedApiKeys().then((result) => {
      setLoadingKeys(false);
      if (!result.ok) {
        setLoadError(result.error);
        setSaved([]);
        return;
      }
      setLoadError("");
      setSaved(result.keys);
    });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleAddKey(e: React.FormEvent) {
    e.preventDefault();
    if (!keyValue.trim() || saving) return;

    setSaving(true);
    setNotice(null);
    try {
      const result = await saveApiKey(provider, keyValue.trim());

      if (!result.ok) {
        setNotice({ tone: "error", text: result.error });
        return;
      }

      // Auto-activate the just-saved provider so it is immediately usable in
      // /chat without another click.
      setActiveProvider({ provider, keyHint: result.key.keyHint });
      setNotice({
        tone: "success",
        text: `Saved ${PROVIDER_LABELS[provider]} key (${result.key.keyHint}). It is now the active key for this tab's chat.`,
      });
      refresh();
    } catch (err) {
      setNotice({
        tone: "error",
        text: err instanceof Error ? err.message : "Could not save your key. Try again.",
      });
    } finally {
      // Clear the key after the attempt, success or failure: the secret must
      // not linger in the field.
      setKeyValue("");
      setSaving(false);
    }
  }

  async function handleDelete(row: SavedApiKey) {
    setNotice(null);
    const result = await deleteApiKey(row.provider);

    if (!result.ok) {
      setNotice({ tone: "error", text: result.error });
      return;
    }

    if (activeProvider?.provider === row.provider) clearActiveProvider();
    setNotice({
      tone: "success",
      text: `Removed the stored ${PROVIDER_LABELS[row.provider]} key.`,
    });
    refresh();
  }

  function handleStopUsing() {
    clearActiveProvider();
    setNotice({ tone: "success", text: "Your key is no longer active in this tab." });
  }

  return (
    <div className="mx-auto w-full max-w-[640px] px-5 pb-16 pt-8">
      <div className="mb-6">
        <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.09em] text-accent">
          Bring your own key
        </p>
        <h1 className="text-[22px] font-semibold leading-tight tracking-[-0.02em] text-foreground">
          API keys
        </h1>
        <p className="mt-2 text-[13px] leading-relaxed text-muted">
          Your provider key is sent to the server once, over HTTPS, and the keys
          are encrypted at rest with a key derived from your account and the
          server&apos;s secret — the raw key is never stored. This tab just
          remembers which saved provider to ask with.
        </p>
      </div>

      <div className="space-y-6">
        {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}

        {activeProvider ? (
          <section className="rounded-2xl border border-emerald-500/25 bg-emerald-500/[0.07] p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[13px] font-medium text-emerald-700 dark:text-emerald-300">
                  Active key for this tab
                </p>
                <p className="mt-1 text-[12.5px] text-muted">
                  {PROVIDER_LABELS[activeProvider.provider]}
                  {activeProvider.keyHint ? ` · ${activeProvider.keyHint}` : ""}
                </p>
              </div>
              <Button type="button" size="sm" variant="secondary" onClick={handleStopUsing}>
                Stop using
              </Button>
            </div>
          </section>
        ) : null}

        <section className="rounded-2xl border border-border-base bg-surface p-5">
          <h2 className="mb-3 text-[15px] font-semibold text-foreground">Saved keys</h2>

          {loadingKeys ? (
            <p className="flex items-center gap-1.5 text-[12.5px] text-muted">
              <SpinnerIcon className="h-3.5 w-3.5" />
              Loading…
            </p>
          ) : loadError ? (
            <Notice tone="error">{loadError}</Notice>
          ) : saved.length === 0 ? (
            <p className="text-[13px] text-muted">
              No keys saved yet. Add one below — it will be encrypted on the
              server before it reaches the key store.
            </p>
          ) : (
            <ul className="space-y-2.5">
              {saved.map((row) => (
                <li
                  key={row.provider}
                  className="flex items-center justify-between gap-3 rounded-xl border border-border-base bg-surface-subtle px-3.5 py-2.5"
                >
                  <div className="min-w-0">
                    <p className="flex items-center gap-1.5 text-[13px] font-medium text-foreground">
                      <KeyIcon className="h-3.5 w-3.5 text-muted" />
                      {PROVIDER_LABELS[row.provider]}
                    </p>
                    <p className="mt-0.5 font-mono text-[12px] text-muted">{row.keyHint}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      disabled={activeProvider?.provider === row.provider}
                      onClick={() =>
                        setActiveProvider({ provider: row.provider, keyHint: row.keyHint })
                      }
                    >
                      {activeProvider?.provider === row.provider ? "Using now" : "Use"}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete ${PROVIDER_LABELS[row.provider]} key`}
                      title="Delete stored key"
                      onClick={() => handleDelete(row)}
                    >
                      <TrashIcon className="h-4 w-4" />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-2xl border border-border-base bg-surface p-5">
          <h2 className="mb-3 text-[15px] font-semibold text-foreground">
            Add or replace a key
          </h2>

          <form onSubmit={handleAddKey} className="space-y-4">
            <div>
              <label
                htmlFor="key-provider"
                className="mb-1.5 block text-[12.5px] font-medium text-foreground"
              >
                Provider
              </label>
              <select
                id="key-provider"
                value={provider}
                onChange={(e) => setProvider(e.target.value as ProviderId)}
                className="h-11 w-full rounded-xl border border-border-base bg-surface px-3.5 text-sm text-foreground outline-none focus:border-accent focus:ring-4 focus:ring-accent/12"
              >
                {PROVIDERS.map((p) => (
                  <option key={p} value={p}>
                    {PROVIDER_LABELS[p]}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor="key-value"
                className="mb-1.5 block text-[12.5px] font-medium text-foreground"
              >
                API key
              </label>
              <Input
                id="key-value"
                type="password"
                autoComplete="off"
                value={keyValue}
                onChange={(e) => setKeyValue(e.target.value)}
                placeholder={KEY_PATTERNS[provider].example}
                aria-invalid={keyValue.trim() !== "" && formatError !== null}
              />
              {keyValue.trim() && formatError ? (
                <p className="mt-1.5 text-[12px] leading-relaxed text-red-600 dark:text-red-400">
                  {formatError}
                </p>
              ) : null}
              <p className="mt-1.5 text-[11.5px] leading-relaxed text-muted">
                Sent once, over HTTPS, and checked with the provider itself
                before it is saved.
              </p>
            </div>

            <div className="flex items-center justify-end gap-2.5">
              <Button
                type="submit"
                size="md"
                disabled={!keyFormatValid || saving}
              >
                {saving ? <SpinnerIcon className="h-4 w-4" /> : "Save key"}
              </Button>
            </div>
          </form>

          <div className="mt-4 flex items-start gap-2 rounded-xl border border-border-base bg-surface-subtle px-3.5 py-3 text-[12.5px] leading-relaxed text-muted">
            <AlertCircleIcon className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              Want your questions answered without adding a key?
              <Link href="/chat" className="ml-1 text-accent hover:underline">
                Chat with the free default model
              </Link>
              — your key only opts you into your own provider.
            </span>
          </div>
        </section>

        <p className="text-center text-[12px] leading-relaxed text-muted">
          Provider keys are yours. We store them encrypted and only ever use the
          key you choose during the current chat session.
        </p>
      </div>
    </div>
  );
}