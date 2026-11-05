import type { Metadata } from "next";

import { AuthGate } from "@/components/AuthGate";
import { FilmsPage } from "@/components/FilmsPage";

export const metadata: Metadata = { title: "Your films" };

export default function Films() {
  return (
    <AuthGate>
      <FilmsPage />
    </AuthGate>
  );
}
