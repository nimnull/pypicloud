Developing
==========

To get set up:

.. code-block:: bash

    $ git clone git@github.com:stevearc/pypicloud
    $ cd pypicloud
    $ python3.14 -m venv pypicloud_env
    $ . pypicloud_env/bin/activate
    $ pip install -r requirements_dev.txt

Run ``ppc-make-config -d development.ini`` to create a developer config file.

Now you can run the server with

.. code-block:: bash

    $ pserve --reload development.ini

The unit tests need redis, MySQL, Postgres, OpenLDAP, DynamoDB Local, azurite
and fake-gcs-server. Tests whose service is unreachable skip themselves. The
``compose`` script starts every service in docker and runs the suite inside the
project container:

.. code-block:: bash

    $ ./compose run tox -e py314

To run the tests against services you started yourself:

.. code-block:: bash

    $ pytest

Lint and type checks:

.. code-block:: bash

    $ ./compose run tox -e lint

Format the code with ``./compose run tox -e format``.
