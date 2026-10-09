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
  clearActiveApiKey,
  setActiveApiKey,
  type ProviderId,
} from "@/lib/api-key-store";
import { useActiveApiKey } from "@/hooks/use-active-api-key";
import { apiKeyHint, decryptApiKey, encryptApiKey } from "@/lib/crypto";
import {
  deleteApiKey,
  getApiKeyBlob,
  listSavedApiKeys,
  saveApiKey,
  type SavedApiKey,
} from "@/app/lib/actions/apiKeys";

/**
 * Manage "bring your own" AI provider keys.
 *
 * The plaintext key is encrypted in this browser with a password-derived
 * AES-GCM key before anything is sent to the backend; the server only ever
 * stores the blob (plus a last-4 hint) in `user_api_keys`. To use a saved key
 * for a chat session the user re-enters the same password, which re-derives
 * the AES key locally and decrypts the blob — the plaintext then lives only in
 * this tab's sessionStorage and is sent per request.
 */
export function ApiKeySettings() {
  const activeKey = useActiveApiKey();

  const [saved, setSaved] = useState<SavedApiKey[]>([]);
  const [loadingKeys, setLoadingKeys] = useState(true);
  const [loadError, setLoadError] = useState("");

  // Two independent passwords: one unlocks saved keys, one encrypts new keys.
  const [unlockPassword, setUnlockPassword] = useState("");
  const [unlockingProvider, setUnlockingProvider] = useState<ProviderId | null>(null);

  // Add/replace form.
  const [provider, setProvider] = useState<ProviderId>("openrouter");
  const [keyValue, setKeyValue] = useState("");
  const [savePassword, setSavePassword] = useState("");
  const [saving, setSaving] = useState(false);

  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

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
    if (!keyValue.trim() || !savePassword || saving) return;

    setSaving(true);
    setNotice(null);
    try {
      const blob = await encryptApiKey(keyValue.trim(), savePassword);
      const hint = apiKeyHint(keyValue);
      const result = await saveApiKey(provider, blob, hint);

      if (!result.ok) {
        setNotice({ tone: "error", text: result.error });
        return;
      }

      setNotice({
        tone: "success",
        text: `Saved ${PROVIDER_LABELS[provider]} key (${result.key.keyHint}). Enter your password and press Unlock to use it this session.`,
      });
      refresh();
    } catch (err) {
      setNotice({
        tone: "error",
        text: err instanceof Error ? err.message : "Could not save your key. Try again.",
      });
    } finally {
      // Clear the key AND password after the attempt, success or failure: the
      // secret must not linger in the field.
      setKeyValue("");
      setSavePassword("");
      setSaving(false);
    }
  }

  async function handleUnlock(row: SavedApiKey) {
    if (!unlockPassword || unlockingProvider) return;

    setUnlockingProvider(row.provider);
    setNotice(null);
    try {
      const blobResult = await getApiKeyBlob(row.provider);

      if (!blobResult.ok) {
        setNotice({ tone: "error", text: blobResult.error });
        return;
      }

      const apiKey = await decryptApiKey(blobResult.encryptedBlob, unlockPassword);
      setActiveApiKey({ provider: row.provider, apiKey, keyHint: blobResult.keyHint || row.keyHint });
      setNotice({
        tone: "success",
        text: `${PROVIDER_LABELS[row.provider]} key unlocked for this session. Ask away in /chat.`,
      });
    } catch {
      setNotice({
        tone: "error",
        text: `Unlock failed — wrong password or corrupted data for ${PROVIDER_LABELS[row.provider]}.`,
      });
    } finally {
      // Clear on success AND failure: the password is a secret and must not
      // stay in the input after an attempt.
      setUnlockPassword("");
      setUnlockingProvider(null);
    }
  }

  async function handleDelete(row: SavedApiKey) {
    setNotice(null);
    const result = await deleteApiKey(row.provider);

    if (!result.ok) {
      setNotice({ tone: "error", text: result.error });
      return;
    }

    if (activeKey?.provider === row.provider) clearActiveApiKey();
    setNotice({
      tone: "success",
      text: `Removed the stored ${PROVIDER_LABELS[row.provider]} key.`,
    });
    refresh();
  }

  function handleLock() {
    clearActiveApiKey();
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
          Your provider key is encrypted in this browser with a password you
          choose — the server only ever stores the encrypted blob and never
          sees the key itself. Forgetting the password means re-entering the
          key; there is no server-side reset.
        </p>
      </div>

      <div className="space-y-6">
        {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}

        {activeKey ? (
          <section className="rounded-2xl border border-emerald-500/25 bg-emerald-500/[0.07] p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[13px] font-medium text-emerald-700 dark:text-emerald-300">
                  Unlocked for this session
                </p>
                <p className="mt-1 text-[12.5px] text-muted">
                  {PROVIDER_LABELS[activeKey.provider]} · {activeKey.keyHint} — sent
                  per request over HTTPS and used once.
                </p>
              </div>
              <Button type="button" size="sm" variant="secondary" onClick={handleLock}>
                Lock
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
              No keys saved yet. Add one below — it is encrypted before it
              reaches the server.
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
                      disabled={!unlockPassword || unlockingProvider !== null}
                      onClick={() => handleUnlock(row)}
                    >
                      {unlockingProvider === row.provider ? (
                        <SpinnerIcon className="h-3.5 w-3.5" />
                      ) : (
                        "Unlock"
                      )}
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

          <div className="mt-4">
            <label
              htmlFor="unlock-password"
              className="mb-1.5 block text-[12.5px] font-medium text-foreground"
            >
              Encryption password
            </label>
            <Input
              id="unlock-password"
              type="password"
              autoComplete="new-password"
              value={unlockPassword}
              onChange={(e) => setUnlockPassword(e.target.value)}
              placeholder="The password this key was saved with"
            />
            <p className="mt-1.5 text-[11.5px] leading-relaxed text-muted">
              Unlocks a saved key for this session. It never leaves your
              browser.
            </p>
          </div>
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
                placeholder={provider === "openrouter" ? "sk-or-…" : "Enter your provider key"}
              />
            </div>

            <div>
              <label
                htmlFor="key-encrypt-password"
                className="mb-1.5 block text-[12.5px] font-medium text-foreground"
              >
                Encryption password
              </label>
              <Input
                id="key-encrypt-password"
                type="password"
                autoComplete="new-password"
                value={savePassword}
                onChange={(e) => setSavePassword(e.target.value)}
                placeholder="Choose a password you will remember"
              />
            </div>

            <div className="flex items-center justify-end gap-2.5">
              <Button
                type="submit"
                size="md"
                disabled={!keyValue.trim() || !savePassword || saving}
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
          Provider keys are yours. We store the encrypted blob, serve it back to
          you, and never attempt to decrypt it.
        </p>
      </div>
    </div>
  );
}