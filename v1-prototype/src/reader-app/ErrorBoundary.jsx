// What Flipparr View shows if a screen fails to draw, instead of a blank
// window: what went wrong, and a way to start again. (A web page can be
// reloaded from the browser; an app has no such button.)
import { Component } from "react";
import { FlipparrMark } from "../brand.jsx";

export class AppErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return <div className="login-shell"><div className="login-card" role="alert">
      <div className="login-brand"><FlipparrMark size={72} /></div>
      <p className="login-error">Something went wrong on this screen: {String(this.state.error?.message || this.state.error)}</p>
      <button type="button" className="primary-button login-submit" onClick={() => window.location.reload()}>Start again</button>
    </div></div>;
  }
}
