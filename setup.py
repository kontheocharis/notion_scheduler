import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="notion_scheduler",
    version="1.0.1",
    author="Constantine Theocharis",
    author_email="cthe@mailbox.org",
    description="Allows the creation of recurring tasks in Notion.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/kontheocharis/notion_scheduler",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.7",
        "License :: OSI Approved :: MIT License",
        "Operating System :: Unix",
    ],
    python_requires='>=3.7',
    install_requires=open('requirements.txt').read().splitlines(),
    entry_points={
        'console_scripts': [
            'notion_scheduler=notion_scheduler.main:main',
        ],
    },
)
