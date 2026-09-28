import { useParams } from "react-router-dom";

export function HostPage() {
  const { hostname } = useParams<{ hostname: string }>();

  return (
    <section>
      <h1>{hostname}</h1>
      <p>Live metric tiles and charts are built in DASH-04 through DASH-07.</p>
    </section>
  );
}
