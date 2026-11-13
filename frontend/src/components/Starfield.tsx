// Decorative, CSS-only drifting star field (see .starfield in globals.css).
export function Starfield() {
  return (
    <div className="starfield" aria-hidden="true">
      <div className="stars stars-small" />
      <div className="stars stars-medium" />
      <div className="stars stars-large" />
    </div>
  );
}
