Developing
==========

To get set up:

.. code-block:: bash

    $ git clone git@github.com:stevearc/pypicloud
    $ cd pypicloud
    $ uv sync --all-extras

Run ``uv run ppc-make-config -d development.ini`` to create a developer config file.

Now you can run the server with

.. code-block:: bash

    $ uv run pserve --reload development.ini

The unit tests need redis, MySQL, Postgres, OpenLDAP, DynamoDB Local, azurite
and fake-gcs-server. Tests whose service is unreachable skip themselves. The
``compose`` script starts every service in docker and runs the suite inside the
project container:

.. code-block:: bash

    $ ./compose test

To run the tests against services you started yourself:

.. code-block:: bash

    $ uv run pytest

Lint and type checks:

.. code-block:: bash

    $ uv run ruff check pypicloud tests scripts
    $ uv run ruff format --check pypicloud tests scripts
    $ uv run mypy pypicloud

Format the code with ``uv run ruff format pypicloud tests scripts``.
