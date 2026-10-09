// A password field with a button to show what was typed: on an iPad's
// keyboard a long password typed blind is easy to get wrong (owner,
// 2026-10-09). Every password field in the web app and Flipparr View uses
// it; PINs keep the PIN pad.
import { useState } from "react";
import { Eye, EyeSlash } from "@phosphor-icons/react";

export function PasswordInput(props) {
  const [shown, setShown] = useState(false);
  return <span className="password-field">
    <input {...props} type={shown ? "text" : "password"}
      // Shown, it is still a password: no capitals, corrections or spelling
      // marks from the keyboard.
      autoCapitalize="none" autoCorrect="off" spellCheck={false} />
    <button type="button" className="password-reveal" onClick={() => setShown((value) => !value)}
      aria-label={shown ? "Hide password" : "Show password"} aria-pressed={shown} title={shown ? "Hide password" : "Show password"}>
      {shown ? <EyeSlash size={18} /> : <Eye size={18} />}
    </button>
  </span>;
}
