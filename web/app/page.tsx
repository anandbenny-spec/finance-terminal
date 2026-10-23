// A0 baseline placeholder (Prompt 01, A0). The shell (C2) and the /policy page (C3) replace it; until then it
// shows no data, so there's nothing here for the accuracy rule to check. The tokens arrived in C1; the shell is C2.
export default function Home() {
  return (
    <main>
      <h1>Finance Terminal</h1>
      <p>Informational only, not investment advice.</p>
    </main>
  );
}
