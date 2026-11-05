import { AuthGate } from "@/components/AuthGate";
import { Composer } from "@/components/Composer";

export default function Home() {
  return (
    <AuthGate>
      <Composer />
    </AuthGate>
  );
}
