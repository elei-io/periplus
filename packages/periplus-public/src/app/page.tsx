import { LandingLauncher } from "@/components/landing-launcher"
import { LandingStory } from "@/components/landing-story"

export default function HomePage() {
  return <main className="discovery-shell">
    <section className="discovery-hero"><span className="eyebrow"><span className="identity-dot" />A public web observatory</span><h1>The web.<br /><span>As one dataset.</span></h1><p>Explore the web through dated observations.{" "}<br className="hidden sm:block" />Filter, compare, and connect what you find. Ask a question or write SQL.</p></section>
    <LandingLauncher />
    <LandingStory />
  </main>
}
