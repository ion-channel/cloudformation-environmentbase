# -*- encoding: utf8 -*-
import io
from os.path import dirname
from os.path import join
from setuptools import find_packages
from setuptools import setup
from multiprocessing import util

exec(open('src/environmentbase/version.py').read())


def read(*names, **kwargs):
    return io.open(
        join(dirname(__file__), *names),
        encoding=kwargs.get("encoding", "utf8")
    ).read()

setup(
    name="cfn-environment-base",

    # Version is centrally managed from src/environmentbase/version.py
    version=__version__,

    description="Base environment for Troposphere based CFN project environments",
    long_description="%s" % read("README.rst"),

    url='https://github.com/ion-channel/cloudformation-environmentbase',

    author="Patrick McClory, Patrick Pierson",
    author_email="patrick@dualspark.com, patrick.pierson@ionchannel.io",

    license="ISC",

    classifiers=[
        # complete classifier list: http://pypi.python.org/pypi?%3Aaction=list_classifiers
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Operating System :: Unix",
        "Operating System :: POSIX",
        "Operating System :: Microsoft :: Windows",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2.6",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Topic :: Utilities",
        "Topic :: Software Development :: Code Generators"
    ],

    # keywords=["keyword1", "keyword2", "keyword3"],

    # List out the packages to include when running build/install/distribute
    packages=find_packages("src", exclude=['tests*']),

    # For more fine grain control over modules included or excluded use py_modules
    # py_modules=[splitext(basename(i))[0] for i in glob.glob("src/**/*.py")],

    # Specifies the root/default package is below 'src'
    package_dir={"": "src"},

    install_requires=[
        "troposphere==1.9.2",
        "jmespath==0.7.1",
        "boto==2.38.0",
        "botocore==1.1.1",
        "boto3==1.1.0",
        "ipcalc==1.1.2",
        "docopt==0.6.2",
        "setuptools==17.1",
        "awacs==0.6.0",
        "commentjson==0.6",
        "PyYAML==3.11",
        "netaddr==0.7.18",
        "toolz==0.7.4",
        'pbr==1.8.0'
    ],

    # Optional dependencies
    extras_require={},

    # This section is required for setuptools to auto-gen the cross platform wrapper script
    # i.e. 'environmentbase --version' instead of 'python -m environmentbase --version'
    entry_points={
        "console_scripts": [
            "environmentbase = environmentbase.__main__:main",
            # "environmentutil = environmentutil.environmentutil:main",
            # "awsbootstrap = environmentbase.accountbootstrap:main"
        ]
    },

    # If disabled, generated egg will only contain *.py files.
    # Leave enabled so we can distribute factory default data w/in the packages
    include_package_data=True,

    # Enable if the package can run as a zip file (some performance improvements)
    # Disable if it needs to run as an extracted zip inside <python>/site-packages
    # Requires special resource handling for embedded data files, see:
    # http://peak.telecommunity.com/DevCenter/PythonEggs#accessing-package-resources
    zip_safe=True,

    # Test runner and required testing packages
    test_suite='nose2.collector.collector',
    tests_require=[
        'nose2==0.5.0',
        'unittest2==1.1.0',
        'mock==1.3.0'
    ]
)
