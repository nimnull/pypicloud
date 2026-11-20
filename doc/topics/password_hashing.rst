:orphan:

.. _password_hashing:
.. _passlib:

A Brief Discussion on Password Hashing
======================================

What is password hashing?
-------------------------
In brief, it is the process of taking a plaintext password and putting it
through a one-way function to produce a resulting hash. Pypicloud uses the
pwdlib library, which hashes with argon2id or bcrypt.

The reason to hash passwords is so that if an attacker gets access to your user
database, they can't read everyone's passwords. The hashes can be used to
*verify* a password that a user provides, but there is no easy way to convert
the hash back into the original password. The main approach to cracking a hash
is to brute force try every possible input until a match is found.  For this
reason, it is useful to have a hashing function that **takes a long time**.  If
it takes a long time for you, it'll take a long time for a hacker that has to
check millions of possible inputs.

There's an excellent discussion of this here: `<https://stackoverflow.com/a/13572232>`__

What is the problem?
--------------------
The *normal* way that a website uses password hashing is that a user will input
their password at most once-per-session, and thereafter they will have a signed
cookie to authenticate themselves. The login process may be slow, but everything
afterwards will be fast.

For pypicloud, you provide an index to pip in the form
``https://user:password@mypypiserver.com``. Every single request made by pip
will send up the username and password because pip cannot store and use a signed
cookie. A typical ``pip install`` can easily make 10 requests to the server. A
hash that takes 350ms costs 3.5 seconds of *pegging* the CPU for one install. If
you have a fleet of servers all hitting pypicloud at the same time, that can
easily become a problematic bottleneck.

In fact, there have been reports from pypicloud users to this effect.
:issue:`115` and :issue:`237` both report too much time being consumed by
password hashing.

What does pypicloud do?
-----------------------
The threat model of pypicloud is somewhat different from a typical website.
Though pypicloud does offer auth backends where users can register accounts, by
far the most common use case is one user account that is shared by a fleet.
There is no worry of exposing user data, and having the password to communicate
to the pypicloud server doesn't help them any more than already having access to
the servers themselves.

Pypicloud hashes with argon2id at the defaults of the hashing library, which
verify in roughly 20ms per request and allocate 64MB of memory while they run.
Memory is the setting to watch: requests hash concurrently, so a server handling
16 parallel pip requests can hold 1GB in hashing alone.

Both settings are tunable, and both schemes are always accepted when verifying,
so switching one does not invalidate the passwords you already stored:

* :ref:`auth.rounds <auth_rounds>` lowers argon2's time cost. ``auth.rounds =
  1`` verifies in about 7ms and keeps the 64MB.
* :ref:`auth.scheme <auth_scheme>` set to ``bcrypt`` uses a scheme whose cost is
  CPU-only. ``auth.scheme = bcrypt`` with ``auth.rounds = 7`` verifies in about
  5ms, which is what the sha512_crypt default of pypicloud 1.x cost.

Those numbers come from one arm64 container and are there for relative scale;
measure on your own hardware before tuning for a fleet.

Upgrading from pypicloud 1.x
----------------------------
Pypicloud 1.x hashed with passlib, defaulting to ``sha512_crypt``. This version
cannot read those hashes. See the upgrade notes in the changelog: every stored
password has to be set again.
