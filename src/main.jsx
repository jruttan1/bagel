import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import CurvedInput from './CurvedInput';
import './style.css';

function App() {
  const [sent, setSent] = useState(false);
  const [phoneError, setPhoneError] = useState('');

  const submitPhone = value => {
    const input = value.trim();
    const digits = input.replace(/\D/g, '');
    if (!input) {
      setPhoneError('Enter your phone number.');
      return;
    }
    if (digits.length < 10 || digits.length > 15) {
      setPhoneError('Enter a valid phone number.');
      return;
    }
    setPhoneError('');
    setSent(true);
  };

  return (
    <main className="shell">
      <div className="topline">
        <div className="brand-lockup">
          <div className="wordmark"><img src="/assets/bagel-mark.svg" alt="" />bagel</div>
        </div>
      </div>

      <section className="panel">
        <div className="hero">
          <h1>A clearer view of<br />your investments.</h1>
          <p className="lede">Bagel keeps up with your portfolio and the market, then texts you what’s worth knowing.</p>
          <div className={`conversation-start ${sent ? 'is-sent' : ''}`}>
            <div className="entry-state" aria-hidden={sent} inert={sent}>
              <CurvedInput
                eyebrowText="Get your morning bagel"
                placeholder="Your phone number"
                buttonText="→"
                type="tel"
                ariaLabel="Your phone number"
                ariaDescribedBy={phoneError ? 'phone-error' : undefined}
                invalid={Boolean(phoneError)}
                width="100%"
                bend={17}
                height={60}
                cornerRadius={30}
                borderWidth={1.2}
                fontSize={15}
                backgroundColor="rgba(239, 226, 195, 0.09)"
                textColor="#fff8e8"
                placeholderColor="rgba(255, 248, 230, 0.78)"
                borderColor={phoneError ? 'rgba(255, 190, 168, 0.92)' : 'rgba(255, 248, 229, 0.58)'}
                buttonColor="rgba(255, 248, 228, 0.31)"
                buttonTextColor="#fff8e9"
                shadowSize="none"
                showIcon={false}
                onChange={() => phoneError && setPhoneError('')}
                onSubmit={submitPhone}
              />
              <small id={phoneError ? 'phone-error' : undefined} className={phoneError ? 'phone-error' : ''} role={phoneError ? 'alert' : undefined}>{phoneError || 'Start a private conversation with Bagel.'}</small>
            </div>
            <div className="success-wrap" role="status" aria-live="polite" aria-hidden={!sent}>
              <span className="success-kicker">Message sent</span>
              <div className="success-card"><span className="success-check"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5.5 12.5 4 4 9-9"/></svg></span><div><strong>Check your texts</strong><small>Bagel will take it from here.</small></div></div>
            </div>
          </div>
        </div>
      </section>

      <p className="legal"><svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3.25" y="7" width="9.5" height="7" rx="2"/><path d="M5.25 7V5.5a2.75 2.75 0 0 1 5.5 0V7"/></svg><span>Fully secure</span></p>
    </main>
  );
}

createRoot(document.getElementById('app')).render(<App />);
