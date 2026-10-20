from setuptools import find_packages, setup

setup(name="aibuds-crawler", version="0.1.0", packages=find_packages(),
      entry_points={"scrapy": ["settings = aibuds_crawler.settings"]})
