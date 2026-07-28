import React, { useEffect, useMemo, useState } from 'react';
import './Connect.css';

const apiBase = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export default function Connect() {
  const token = useMemo(() => new URLSearchParams(window.location.search).get('token') || '', []);
  const [linkState, setLinkState] = useState('checking');
  const [phoneHint, setPhoneHint] = useState('');
  const [otpRequired, setOtpRequired] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!token) {
      setLinkState('invalid');
      return;
    }
    fetch(`${apiBase}/api/v1/wealthsimple/connection/${encodeURIComponent(token)}`)
      .then(response => response.json())
      .then(body => {
        setPhoneHint(body.phone_hint || '');
        setLinkState(body.valid ? 'ready' : 'invalid');
      })
      .catch(() => setLinkState('invalid'));
  }, [token]);

  const connect = async event => {
    event.preventDefault();
    setError('');
    setSubmitting(true);
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetch(`${apiBase}/api/v1/wealthsimple/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token,
          username: data.get('username'),
          password: data.get('password'),
          otp: data.get('otp') || null,
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || 'Wealthsimple could not be connected.');
      if (body.status === 'otp_required') {
        setOtpRequired(true);
        setError('Enter the verification code Wealthsimple sent you.');
      } else {
        setLinkState('connected');
      }
    } catch (requestError) {
      setError(requestError.message || 'Wealthsimple could not be connected.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="connect-shell">
      <div className="connect-brand"><img src="/assets/bagel-mark.svg" alt="" />bagel</div>
      <section className="connect-card">
        {linkState === 'checking' && <p>Checking your secure link…</p>}
        {linkState === 'invalid' && <><h1>This link has expired.</h1><p>Text Bagel for a fresh connection link.</p></>}
        {linkState === 'connected' && <><span className="connect-done">✓</span><h1>Wealthsimple is connected.</h1><p>You can close this page. Bagel will continue in your texts.</p></>}
        {linkState === 'ready' && <>
          <span className="connect-label">Secure connection {phoneHint && `for ${phoneHint}`}</span>
          <h1>Connect Wealthsimple</h1>
          <p>Your password is used only to establish an encrypted session and is never stored.</p>
          <form onSubmit={connect}>
            <label>Email<input name="username" type="email" autoComplete="username" required /></label>
            <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
            {otpRequired && <label>Verification code<input name="otp" inputMode="numeric" autoComplete="one-time-code" required /></label>}
            {error && <small className="connect-error" role="alert">{error}</small>}
            <button type="submit" disabled={submitting}>{submitting ? 'Connecting…' : otpRequired ? 'Verify and connect' : 'Connect securely'}</button>
          </form>
        </>}
      </section>
      <p className="connect-security">⌑ Encrypted in transit · Password never stored</p>
    </main>
  );
}
