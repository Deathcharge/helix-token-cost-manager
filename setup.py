from setuptools import setup, find_packages

setup(
    name="helix-token-cost-manager",
    version="0.1.0",
    description="Token tracking and cost calculation for LLM usage",
    author="Helix Collective",
    author_email="collective@helix.ai",
    url="https://github.com/Deathcharge/helix-token-cost-manager",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "helix-hub-shared>=0.1.0",
        "pydantic>=2.0",
    ],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)
