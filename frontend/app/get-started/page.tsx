import { Suspense } from "react";
import { RepoSetup } from "@/components/repo-setup";

export const metadata = {
  title: "Connect a repository",
  description: "Paste a GitHub repository URL to start asking questions about it.",
};

export default function GetStartedPage() {
  // RepoSetup reads search params, which requires a Suspense boundary.
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center px-5">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-border-base border-t-accent" />
        </div>
      }
    >
      <RepoSetup />
    </Suspense>
  );
}
