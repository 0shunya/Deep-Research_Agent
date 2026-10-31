import { KeyRound } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { setAccessCode } from "../lib/api";
import { Button, Label, TextInput } from "./ui";

/** Shown when the deployment sets DEMO_ACCESS_CODE and the browser has no valid code yet. */
export default function AccessGate({ onUnlock, invalid }: { onUnlock: () => void; invalid: boolean }) {
  const [code, setCode] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    setAccessCode(code.trim());
    onUnlock();
  }
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-md border border-line bg-surface p-6">
        <KeyRound className="mb-3 size-5 text-accent" aria-hidden />
        <h1 className="text-lg font-semibold">Access code required</h1>
        <p className="mt-1 text-ink-2">This demo deployment is protected so the model and search quota are not open to everyone.</p>
        <div className="mt-4">
          <Label htmlFor="access-code">Access code</Label>
          <TextInput id="access-code" type="password" autoFocus value={code} onChange={(e) => setCode(e.target.value)} />
          {invalid && <p className="mt-1.5 text-[13px] text-bad">That code was not accepted.</p>}
        </div>
        <Button variant="primary" type="submit" className="mt-4 w-full" disabled={!code.trim()}>Continue</Button>
      </form>
    </div>
  );
}
